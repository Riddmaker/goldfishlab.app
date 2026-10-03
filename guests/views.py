"""Try it without an account, then save it (phase 9 G).

The flow the user decided on 2026-09-28, exactly:

    drop a CSV (no account, no deck name asked) -> the simulation starts by
    itself -> "Save this deck - free account" -> deck name, email, password
    -> "we sent you a link" -> click it -> signed in, on the saved deck.
"""

from allauth.account.internal import flows
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.decorators import rate_limit
from django.contrib import messages
from django.contrib.auth import logout
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import FormView
from django_ratelimit.core import is_ratelimited
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from billing.quotas import QuotaExceeded
from billing.views import refusal
from decks.views import ImportFlowMixin, imported
from guests import services
from guests.forms import SaveDeckForm
from simulations import services as simulations


def land(request, outcome):
    """Where a guest's import ends: straight into its first simulation.

    Only a clean import runs by itself. Rows that did not match go to the
    review first, as for everybody - a report on a deck with missing cards
    would answer a question nobody asked.
    """
    if not outcome.clean:
        return imported(request, outcome)
    try:
        run = simulations.start_run(
            owner=request.user,
            deck=outcome.deck,
            games=services.TRIAL_GAMES,
            turns=services.TRIAL_TURNS,
            on_the_play=False,
        )
    except QuotaExceeded as exc:
        # The sentence, not the exception's text: that is a log line.
        messages.info(request, refusal(exc))
        return redirect(outcome.deck.get_absolute_url())
    except simulations.SimulationRefused as exc:
        messages.info(request, str(exc))
        return redirect(outcome.deck.get_absolute_url())
    return redirect(run.get_absolute_url())


# Per address, because a guest is not anybody yet: ten uploads a minute is
# someone fixing their export, not someone filling the database.
@method_decorator(ratelimit(key="ip", rate="10/m", method="POST"), name="post")
class TryView(ImportFlowMixin, FormView):
    """The importer, open to anybody, owned by a guest made on the way in."""

    template_name = "guests/try.html"

    #: New guests per address. Each is a user row until it expires, so this
    #: is the number that bounds the table rather than the request rate. A
    #: five-minute window, because the privacy policy promises that no
    #: rate-limit counter holds an address for longer.
    NEW_GUESTS_RATE = "5/5m"

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and not services.is_guest(user):
            return redirect("decks:import")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if services.is_guest(self.request.user):
            context["guest_deck"] = services.deck_of(self.request.user)
        return context

    def import_owner(self):
        if services.is_guest(self.request.user):
            services.reset(self.request.user)
            return self.request.user
        if is_ratelimited(self.request, group="guests.create", key="ip",
                          rate=self.NEW_GUESTS_RATE, increment=True):
            raise Ratelimited()
        return services.create(self.request)

    def landed(self, outcome):
        return land(self.request, outcome)


@method_decorator(sensitive_post_parameters("password1"), name="dispatch")
@method_decorator(never_cache, name="dispatch")
@method_decorator(rate_limit(action="signup"), name="dispatch")
class SaveDeckView(FormView):
    """Turn the guest into an account, keeping the deck.

    Not allauth's `SignupView`: that one sends every signed-in visitor away
    (`RedirectAuthenticatedUserMixin`), and a guest is signed in. Everything
    else is allauth's own - the form, `try_save`, `complete_signup` - and so
    is the sign-up rate limit, under the same action name.

    **The order is the design.** The guest is signed out *before* the account
    is made: allauth signs in on the confirmation link only if nobody is
    signed in in that browser (`login_on_verification`), and it keeps the
    pending login - with the deck's address as where to land - in the
    session. Signing the guest out afterwards would flush exactly that.
    """

    template_name = "guests/save.html"
    form_class = SaveDeckForm

    def dispatch(self, request, *args, **kwargs):
        if not services.is_guest(request.user):
            return redirect("decks:list" if request.user.is_authenticated else "guests:try")
        self.deck = services.deck_of(request.user)
        if self.deck is None:
            return redirect("guests:try")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"deck_name": self.deck.name}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["deck"] = self.deck
        return context

    def form_valid(self, form):
        guest, deck = self.request.user, self.deck
        logout(self.request)
        try:
            with transaction.atomic():
                user, response = form.try_save(self.request)
                if response:
                    # The address already has an account. allauth mails its
                    # owner and answers as if it had worked, so the page does
                    # not tell a stranger which addresses are registered. The
                    # guest's deck stays with the guest and expires with it.
                    return response
                services.claim(guest, user, deck_name=form.cleaned_data["deck_name"])
            return flows.signup.complete_signup(
                self.request, user=user, redirect_url=reverse("decks:detail", args=[deck.pk])
            )
        except ImmediateHttpResponse as exc:
            return exc.response
