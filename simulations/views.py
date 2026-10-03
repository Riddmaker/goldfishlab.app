"""Screens for starting a simulation, watching it, reading it, and correcting it.

Every queryset here is filtered by `owner=self.request.user` at the source, the
same rule the deck views follow: a missing filter has to produce a missing row,
never someone else's simulation.

The progress fragment is the only unusual piece. It answers with a partial page
carrying its own `hx-trigger`, and when the run reaches a terminal state the
fragment comes back **without** the trigger - so polling stops because there is
nothing left to poll with. No JavaScript is written by hand anywhere in this
application.

The second half of the module is Phase 4: the screens that let somebody find
out what the engine read off their cards, and say so when it read them wrong.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import DetailView, View
from django_ratelimit.decorators import ratelimit

from billing import quotas
from billing import views as billing_views
from billing.models import UsageRecord
from billing.quotas import QuotaExceeded
from cards.models import OracleCard
from decks.models import Deck
from guests.services import LIFETIME as GUEST_LIFETIME
from simulations import (
    blindspots,
    provenance,
    report,
    review,
    services,
    strategies,
    summary,
)
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE, USER_SCOPE
from simulations.forms import AnnotationForm, RunForm
from simulations.models import SimulationRun


class OwnedRunsMixin(LoginRequiredMixin):
    """Scope every run lookup to the signed-in user."""

    def get_queryset(self):
        return SimulationRun.objects.filter(owner=self.request.user).select_related("deck")


@method_decorator(ratelimit(key="user", rate="20/m", method="POST"), name="post")
class RunCreateView(LoginRequiredMixin, View):
    """Start a simulation for one deck.

    The quota already bounds how many games an account may simulate in a month
    and refunds them when a run is cancelled. That is a budget, not a valve:
    twenty small runs enqueued in a second are twenty chords of Celery tasks,
    and the quota is perfectly happy with all of them. This is the valve.

    Twenty a minute is far above anybody clicking a button and far below a loop.
    """

    def post(self, request, deck_id):
        deck = get_object_or_404(Deck, pk=deck_id, owner=request.user)
        form = RunForm(request.POST, plan=quotas.plan_for(request.user), trim=False)

        if not form.is_valid():
            messages.error(request, "That is not a size this application runs.")
            return redirect(deck.get_absolute_url())

        if not deck.entries.exists():
            messages.error(request, "There is nothing in this deck to simulate yet.")
            return redirect(deck.get_absolute_url())

        try:
            run = services.start_run(
                owner=request.user,
                deck=deck,
                games=form.cleaned_data["games"],
                turns=form.cleaned_data["turns"],
                on_the_play=form.cleaned_data["on_the_play"],
            )
        except QuotaExceeded as exc:
            # Not a message on the deck page. A refusal the person cannot act
            # on is a dead end, and the one thing that answers "you have used
            # all twenty runs" is the page listing the tier with three hundred.
            return billing_views.upgrade_prompt(request, str(exc))
        except services.SimulationRefused as exc:
            messages.error(request, str(exc))
            return redirect(deck.get_absolute_url())

        return redirect(run.get_absolute_url())


def _pace(run: SimulationRun) -> dict | None:
    """What the smoothed progress bar needs (phase 10 T3.1), while it runs.

    Elapsed time is counted here, on the server's clock, so a visitor whose
    own clock is wrong still sees the bar start where it should - from the
    moment the run was asked for while it waits in the queue, from the moment
    a worker started it once it plays. A queue can be long, and the script
    holds the bar low while `queued` says so.
    """
    if run.is_finished:
        return None
    since = run.started_at or run.created_at
    return {
        "elapsed": (timezone.now() - since).total_seconds(),
        "expected": services.expected_seconds(run),
        "queued": run.started_at is None,
    }


class RunDetailView(OwnedRunsMixin, DetailView):
    """One run: the progress bar while it works, the report once it is done."""

    template_name = "simulations/detail.html"
    context_object_name = "run"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run = self.object
        context["pace"] = _pace(run)
        if run.status == SimulationRun.Status.DONE and run.result:
            context["report"] = report.build(run)
            # Read from the deck as it stands now, not from the run: these are
            # properties of the cards rather than of the simulation, and the
            # panel says so rather than implying the run measured them.
            readings = adapter.readings(run.deck)
            context["blindspots"] = blindspots.find(readings)
            # "None in the deck: ..." under "By category" (phase 11 D, P3).
            context["missing_roles"] = summary.missing(readings)
            # The written part (phase 10 H), unless the viewer switched it off.
            if self.request.user.deck_summaries:
                context["written"] = summary.state(self.request.user, run.deck)
            # "Your strategies, and what would feed them" (phase 11 E): with
            # Mistral's picks when it made some, its fallback otherwise.
            written = context.get("written") or {}
            context["strategies"] = strategies.block(readings, written.get("content"))
            # The deck as it stands now, like the blind spots: "cards that need
            # your attention" is about what a person can still do.
            context["open_questions"] = review.open_questions(run.deck)
            context["guest_hours"] = int(GUEST_LIFETIME.total_seconds() // 3600)
        return context


class RunProgressView(OwnedRunsMixin, DetailView):
    """The polling fragment.

    Deliberately tiny and deliberately dumb: one row from the database, no
    broker call. That is what makes polling it every two seconds cheap enough
    to be the whole mechanism.

    When the run has finished, the answer to a poll is `HX-Refresh`, which
    tells htmx to reload the page - so the report appears by itself, the
    polling stops with it, and still not a line of JavaScript was written.
    """

    template_name = "simulations/_progress.html"
    context_object_name = "run"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pace"] = _pace(self.object)
        return context

    def render_to_response(self, context, **kwargs):
        response = super().render_to_response(context, **kwargs)
        if self.object.is_finished and self.request.headers.get("HX-Request"):
            response.headers["HX-Refresh"] = "true"
        return response


class RunSummaryView(OwnedRunsMixin, DetailView):
    """The deck summary while it is being written: the progress view's trick.

    Answers with the small "Writing your deck summary" line, carrying its own
    `hx-trigger`, while the row is pending; once it is not, `HX-Refresh`, and
    the page reloads with the text in its place.
    """

    template_name = "simulations/_summary_pending.html"
    context_object_name = "run"

    def render_to_response(self, context, **kwargs):
        from simulations.models import DeckSummary

        response = super().render_to_response(context, **kwargs)
        pending = DeckSummary.objects.filter(
            deck=self.object.deck, status=DeckSummary.Status.PENDING).exists()
        if not pending and self.request.headers.get("HX-Request"):
            response.headers["HX-Refresh"] = "true"
        return response


@method_decorator(ratelimit(key="user", rate="10/m", method="POST"), name="post")
class SummaryWriteView(OwnedRunsMixin, View):
    """"Write a summary for this deck" (phase 10 H, P8): one click, one run.

    The fifth call site of `quotas.check()`. Posted from a run page, and back
    to it: the run is fetched through the owner's runs, so a guessed id is a
    404, and the deck is that run's.
    """

    def post(self, request, pk):
        run = get_object_or_404(self.get_queryset(), pk=pk)
        back = f"{run.get_absolute_url()}#summary"
        user = request.user
        # Nothing to write - current, already being written, switched off, a
        # guest, no Mistral: back to the page, which shows why.
        if user.is_guest or not summary.due(user, run.deck):
            return redirect(back)
        try:
            quotas.check(user, UsageRecord.Metric.RUNS_STARTED)
        except QuotaExceeded as exc:
            return billing_views.upgrade_prompt(request, str(exc))
        with transaction.atomic():
            if summary.claim(user, run.deck):
                quotas.consume(user, UsageRecord.Metric.RUNS_STARTED)
                summary.begin(run.deck, charged=True)
        return redirect(back)


class SummarySwitchView(LoginRequiredMixin, View):
    """Deck summaries on or off, for the whole account (phase 10 H, T6.5).

    "Hide summaries" on the run page, "Show deck summaries" on "Your plan".
    Back to where it was pressed, if that is a page of ours.
    """

    def post(self, request):
        request.user.deck_summaries = request.POST.get("on") == "1"
        request.user.save(update_fields=["deck_summaries"])
        if request.user.deck_summaries:
            messages.success(request, "Deck summaries are on again.")
        else:
            messages.success(request, "Deck summaries are off. Switch them on again on Your plan.")
        target = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
            target = reverse("billing:plans")
        return redirect(target)


class RunCancelView(OwnedRunsMixin, View):
    """Ask a run to stop."""

    def post(self, request, pk):
        run = get_object_or_404(self.get_queryset(), pk=pk)
        if services.request_cancel(run):
            messages.success(
                request,
                "Cancelling. The chunk already in flight finishes first, so this "
                "takes a few seconds.",
            )
        else:
            messages.info(request, "That run had already finished.")
        return redirect(run.get_absolute_url())


# --- the honesty layer ------------------------------------------------------
#
# The reason these screens exist: a result nobody can check is a result nobody
# should believe. The card page says what the engine reads off a card and where
# each reading came from, and lets a human disagree; the deck page's grid is the
# way to every card (the separate card list went in phase 9 I). The deck's
# casting order went in Phase 9 C: the product is statistics about a deck, not
# steering a game, and the engine's own rule - cheapest first - is an answer
# nobody has to be asked for.
#
# Ownership works the same way as everywhere else in this application: the
# deck is fetched filtered by owner, and the card is fetched filtered by deck
# membership. A card id that is not in the deck is a 404, not a saved row
# against somebody else's deck.


class DeckScopedView(LoginRequiredMixin, View):
    """Anything that edits one deck's annotations.

    `deck` comes out of a queryset filtered by owner, so a deck belonging to
    somebody else is missing rather than forbidden - the same rule the deck
    views follow, for the same reason.
    """

    def get_deck(self, request, pk) -> Deck:
        return get_object_or_404(Deck, pk=pk, owner=request.user)

    def get_card(self, deck: Deck, oracle_id):
        """One card of this deck, commander included.

        The commander is not a `DeckCard` - it sits in the command zone - so
        filtering on deck membership alone would make it un-annotatable, which
        is how it lost its priority once before.
        """
        return get_object_or_404(
            OracleCard.objects.filter(
                Q(in_decks__deck=deck) | Q(commands_decks=deck)
            ).distinct(),
            pk=oracle_id,
        )


def _cards_of(deck) -> str:
    """The deck page's card grid - the one list of a deck's cards."""
    return f"{deck.get_absolute_url()}#cards"


def tune_moved(request, pk):
    """The old card list ("What the engine reads"), gone in phase 9 I.

    Since D the deck page shows the same grid, with the same markers and
    filters on top, so an old link or bookmark lands there. Nothing is looked
    up: the deck page does its own owner check, and a redirect reveals nothing.
    A temporary redirect, so no browser remembers it for good.
    """
    return redirect(f"{reverse('decks:detail', args=[pk])}#cards")


class DeckReviewView(DeckScopedView):
    """The way in from the red marker: the first card that still needs you.

    Nothing left to answer is "Ready", said on the deck page, which is where
    the marker that led here is.
    """

    def get(self, request, pk):
        deck = self.get_deck(request, pk)
        questions = review.queue(deck)
        first = next((question for question in questions if not question.answered), None)
        if first is None:
            messages.success(request, READY)
            return redirect(deck.get_absolute_url())
        return redirect(reverse("simulations:annotate", args=[deck.pk, first.oracle_card.pk]))


#: What the deck page says once every card the engine could not read is answered.
READY = "Ready. Every card the engine could not read has an answer."


class CardAnnotateView(DeckScopedView):
    """One card: what the engine reads, and a form to disagree with it.

    A card the engine could not read is also one step of the review (Phase 9
    C2): the page says "2 of 5", and Save and next, Looks right, Skip and Back
    walk the same stable queue the red marker counts.
    """

    def get(self, request, pk, oracle_id):
        deck = self.get_deck(request, pk)
        card = self.get_card(deck, oracle_id)
        scope = self._scope(request.GET.get("scope"))
        annotation = services.annotation_at(deck, card, scope)

        return render(request, "simulations/annotate.html", self._context(
            deck, card,
            form=AnnotationForm(
                initial={"scope": scope, **AnnotationForm.initial_for(annotation)}
            ),
            annotation=annotation,
            scope=scope,
        ))

    def post(self, request, pk, oracle_id):
        deck = self.get_deck(request, pk)
        card = self.get_card(deck, oracle_id)

        if request.POST.get("action") == "confirm":
            scope = self._scope(request.POST.get("scope"))
            services.confirm_annotation(deck=deck, oracle_card=card, scope=scope)
            messages.success(request, f"Noted: {card.front_name} looks right to you.")
            return self._onwards(request, deck, card, scope)

        form = AnnotationForm(request.POST)
        if form.is_valid():
            scope = form.cleaned_data["scope"]
            try:
                services.save_annotation(
                    deck=deck, oracle_card=card, scope=scope,
                    judgements=form.judgements(),
                    note=form.cleaned_data.get("note", ""),
                )
            except ValidationError as exc:
                # The model validates on every save, so a colour the engine
                # would not recognise arrives here rather than in a worker.
                form.add_error(None, exc.messages)
            else:
                messages.success(
                    request,
                    f"Saved. {card.front_name} will be read that way from the "
                    "next run onwards — this one does not change a result that "
                    "has already been computed.",
                )
                return self._onwards(request, deck, card, scope)

        scope = self._scope(form.data.get("scope"))
        return render(request, "simulations/annotate.html", self._context(
            deck, card, form=form,
            annotation=services.annotation_at(deck, card, scope),
            scope=scope,
        ), status=400)

    def _onwards(self, request, deck, card, scope):
        """After an answer: the next card still open, else the deck, "Ready".

        A card that was never in the queue goes back to the deck's card grid,
        where its owner came from.
        """
        questions = review.queue(deck)
        ids = [question.oracle_card.pk for question in questions]
        if card.pk not in ids:
            return redirect(_cards_of(deck))
        here = ids.index(card.pk)
        # From here to the end, then round from the start: "next" is the next
        # one that still needs an answer, wherever it is.
        for question in questions[here + 1:] + questions[:here]:
            if not question.answered:
                return redirect(self._url(deck, question.oracle_card, scope))
        messages.success(request, READY)
        return redirect(deck.get_absolute_url())

    @staticmethod
    def _url(deck, oracle_card, scope) -> str:
        url = reverse("simulations:annotate", args=[deck.pk, oracle_card.pk])
        return f"{url}?scope={scope}" if scope != DECK_SCOPE else url

    def _context(self, deck, card, *, form, annotation, scope) -> dict:
        entries = provenance.for_deck(deck)
        entry = next(entry for entry in entries if entry.oracle_card.pk == card.pk)
        questions = review.queue(deck, [each.reading for each in entries])
        ids = [question.oracle_card.pk for question in questions]
        first, more = form.split(
            {gap.field for gap in entry.reading_gaps}, is_land=entry.reading.card.is_land,
        )
        context = {
            "deck": deck,
            "card": card,
            "entry": entry,
            "form": form,
            "first_fields": first,
            "more_fields": more,
            "annotation": annotation,
            "scope": scope,
            "step": None,
        }
        if card.pk in ids:
            here = ids.index(card.pk)
            context["step"] = {
                "position": here + 1,
                "total": len(questions),
                "open": sum(1 for question in questions if not question.answered),
                "back": self._url(deck, questions[here - 1].oracle_card, scope)
                if here else None,
                "skip": self._url(deck, questions[here + 1].oracle_card, scope)
                if here + 1 < len(questions) else deck.get_absolute_url(),
            }
        return context

    @staticmethod
    def _scope(value) -> str:
        """A scope from the query string, or the narrowest one.

        Narrowest by default, and never `builtin`: a judgement about one deck
        that silently applied to every deck of every user would be the worst
        possible default, so an unrecognised value falls back to this deck.
        """
        return value if value in {DECK_SCOPE, USER_SCOPE} else DECK_SCOPE


class AnnotationDeleteView(DeckScopedView):
    """Throw one judgement away and go back to the derived reading."""

    def post(self, request, pk, oracle_id):
        deck = self.get_deck(request, pk)
        card = self.get_card(deck, oracle_id)
        scope = CardAnnotateView._scope(request.POST.get("scope"))

        if services.delete_annotation(deck=deck, oracle_card=card, scope=scope):
            messages.success(
                request,
                f"Removed. {card.front_name} is back to what the card data says.",
            )
        else:
            messages.info(request, "There was nothing recorded for that card.")
        return redirect(_cards_of(deck))
