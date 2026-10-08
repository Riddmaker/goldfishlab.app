"""Billing screens, and the one endpoint Stripe talks to.

Four of these are ordinary signed-in pages scoped to `request.user`. The fifth
is the webhook, and it is the only view in this application that is
**unauthenticated, CSRF-exempt and POST-only** - which is exactly why the
signature check is the first thing it does and why it never trusts a single
field until `construct_event` has returned.

The success redirect grants nothing. A person can type that URL. All it does is
say "thank you, it may take a moment", and the page reflects whatever the
webhook has already written.
"""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext, ngettext
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View

from billing import currency, quotas, services, stripe_api
from billing.models import Plan

logger = logging.getLogger(__name__)


class PlansView(LoginRequiredMixin, TemplateView):
    """What each tier costs and what it buys.

    The table is read from the database rather than written in the template,
    because the limits live there - changing what the free plan allows is a
    data edit, and a page that restated the numbers would start lying on the
    first such edit.
    """

    template_name = "billing/plans.html"

    def get_context_data(self, **kwargs):
        context = {**super().get_context_data(**kwargs), **_tiers(self.request)}
        subscription = services.subscription_for(self.request.user)
        context["subscription"] = subscription
        context["usage"] = _usage_rows(self.request.user)
        # The deck summary's switch (phase 10 H, T6.5) is turned on again here.
        from simulations import mistral

        context["summaries_available"] = mistral.is_configured()
        # C7: the changelog mail's switch, beside the language.
        from changelog import services as changelog

        context["changelog_mail_on"] = changelog.mail_on()
        return context


class PricingView(TemplateView):
    """The tiers for anybody, signed in or not (P3).

    /billing/ is the signed-in account's own page and a guest is sent away from
    it, so until P3 a visitor - or a search engine - could not see what the
    tiers cost. This page shows the same cards from the same rows (`_tiers`),
    so the two cannot disagree - the free plan, and each paid one as soon as
    it can be bought. A member is sent to their own page, which has the
    buttons.
    """

    template_name = "billing/pricing.html"

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and not user.is_guest:
            return redirect("billing:plans")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        tiers = _tiers(self.request)
        # A paid tier is shown in public once it can be bought, not before:
        # until then its name, price and limits are still being decided.
        tiers["plans"] = [plan for plan in tiers["plans"]
                          if plan.is_default or plan.pk in tiers["purchasable"]]
        return {**super().get_context_data(**kwargs), **tiers}


def _tiers(request) -> dict:
    """The active plans with their price in this visitor's currency, and
    which of them can be bought right now."""
    plans = list(Plan.objects.filter(is_active=True).order_by("price_chf_cents"))
    shown = currency.for_request(request)
    for plan in plans:
        # Phase 11 G: "CHF 4", "€4" or "$4" while LOCAL_PRICES is on.
        cents = plan.price_cents(shown)
        plan.price_label = currency.label(cents, shown) if cents else ""
        annual = plan.annual_cents(shown)
        plan.annual_label = currency.label(annual, shown) if annual else ""
    return {"plans": plans,
            "purchasable": {plan.pk for plan in services.purchasable_plans()},
            "can_pay": stripe_api.is_configured()}


def _usage_rows(user) -> list[dict]:
    """This month's consumption against the plan, for the page.

    Read through `quotas.check` with `raise_on_fail=False` so the page and the
    enforcement cannot disagree: if this said "3 of 20" while the enqueue path
    refused, the number on screen would be the wrong one.
    """
    from billing.models import UsageRecord

    rows = []
    for metric, label, period in (
        (UsageRecord.Metric.RUNS_STARTED, gettext("Simulations started"),
         gettext("this month")),
        # Decks you have, not decks made this month: deleting one frees a slot.
        (quotas.DECKS_OWNED, gettext("Decks"), gettext("right now")),
    ):
        decision = quotas.check(user, metric, amount=0, raise_on_fail=False)
        # The bar's width; capped, because a plan changed mid-month can leave
        # more used than the new limit allows.
        pct = 0 if decision.unlimited or not decision.limit else min(
            100, round(100 * decision.used / decision.limit))
        rows.append({"label": label, "period": period, "used": decision.used,
                     "limit": decision.limit, "unlimited": decision.unlimited,
                     "pct": pct})
    return rows


class StartCheckoutView(LoginRequiredMixin, View):
    """Hand the person to Stripe's hosted Checkout."""

    def post(self, request, slug):
        plan = Plan.objects.filter(slug=slug, is_active=True).first()
        if plan is None or plan.is_default:
            return HttpResponseBadRequest("No such plan.")

        base = request.build_absolute_uri
        try:
            url = services.start_checkout(
                request.user, plan,
                success_url=base(reverse("billing:done")),
                cancel_url=base(reverse("billing:plans")),
                terms_url=base(reverse("terms")),
                currency=currency.for_request(request),
                interval="year" if request.POST.get("interval") == "year" else "month",
            )
        except services.BillingNotConfigured as exc:
            messages.error(request, str(exc))
            return redirect("billing:plans")
        except services.AlreadySubscribed as exc:
            messages.info(request, str(exc))
            return redirect("billing:plans")
        except stripe_api.StripeError:
            # The message is deliberately not Stripe's. Its text can name a
            # price id or an account, and this page is shown to a stranger.
            logger.exception("stripe checkout failed for user %s", request.user.pk)
            messages.error(request, gettext("Stripe could not start a checkout just now. "
                                            "Nothing was charged. Please try again."))
            return redirect("billing:plans")

        return redirect(url)


class PortalView(LoginRequiredMixin, View):
    """Hand the person to Stripe's hosted Customer Portal.

    Cancelling, changing a card and downloading an invoice all happen there.
    This application has no cancel button of its own on purpose: a second place
    that can end a subscription is a second place that can disagree with
    Stripe about whether it ended.
    """

    def post(self, request):
        try:
            url = services.start_portal(
                request.user,
                return_url=request.build_absolute_uri(reverse("billing:plans")),
            )
        except services.BillingNotConfigured as exc:
            messages.error(request, str(exc))
            return redirect("billing:plans")
        except stripe_api.StripeError:
            logger.exception("stripe portal failed for user %s", request.user.pk)
            messages.error(request,
                           gettext("Stripe could not open the billing portal just now."))
            return redirect("billing:plans")

        return redirect(url)


class CheckoutDoneView(LoginRequiredMixin, TemplateView):
    """Where Stripe sends somebody after they pay.

    **This page grants nothing.** It is reachable by typing the URL, it arrives
    before the webhook sometimes, and believing it would mean believing the
    browser about money. So it shows whatever the webhook has already written
    and says plainly that the rest is on its way.
    """

    template_name = "billing/done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["subscription"] = services.subscription_for(self.request.user)
        return context


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    """The only thing in this application that believes a statement about money.

    Unauthenticated and CSRF-exempt because Stripe is not a browser and has no
    session; safe because `construct_event` verifies an HMAC over the raw body
    with the endpoint's own secret, inside a 300-second replay window.

    **Answering 200 is the goal, not the courtesy.** Stripe retries anything
    else with backoff for days, so a 500 on an event we simply have no use for
    would turn one unhandled type into a retry storm. The event is recorded
    first and the work is idempotent, so a retry we *do* get changes nothing.
    """

    def post(self, request):
        if not stripe_api.is_configured():
            # Refusing rather than accepting: an installation with no keys
            # cannot have a webhook secret either, so anything arriving here is
            # misdirected and silently swallowing it would hide that.
            return HttpResponseBadRequest("Billing is not configured.")

        try:
            event = stripe_api.construct_event(
                request.body, request.headers.get("Stripe-Signature")
            )
        except stripe_api.SignatureError:
            logger.warning("rejected a webhook with a bad signature")
            return HttpResponseBadRequest("Bad signature.")
        except ValueError:
            return HttpResponseBadRequest("Unparseable payload.")

        outcome = services.apply_event(event)
        logger.info("stripe %s: %s", event["type"], outcome)
        return HttpResponse(outcome, content_type="text/plain")


def upgrade_prompt(request, exc: quotas.QuotaExceeded):
    """A quota refusal, pointed at the page that can fix it.

    Imported by the deck and simulation views so that "you have used all twenty
    runs" is one sentence away from the tier that has three hundred, rather
    than a dead end with an apology.
    """
    return render(request, "billing/blocked.html", {
        "message": refusal(exc),
        "subscription": services.subscription_for(request.user),
    }, status=402)


def refusal(exc: quotas.QuotaExceeded) -> str:
    """What ran out, as a sentence in the page's language (phase 12).

    Built from the exception's numbers rather than its message, which is a
    log line ("runs_started: 20/20 used on the Free plan").
    """
    from billing.models import UsageRecord

    numbers = {"used": exc.used, "limit": exc.limit}
    if exc.metric == UsageRecord.Metric.RUNS_STARTED:
        return ngettext("You have started %(used)s of %(limit)s simulation this month.",
                        "You have started %(used)s of %(limit)s simulations this month.",
                        exc.limit) % numbers
    if exc.metric == quotas.DECKS_OWNED:
        return ngettext("You have %(used)s of %(limit)s deck.",
                        "You have %(used)s of %(limit)s decks.", exc.limit) % numbers
    if exc.metric == UsageRecord.Metric.IMPORTS:
        return ngettext("You have imported %(used)s of %(limit)s deck list this month.",
                        "You have imported %(used)s of %(limit)s deck lists this month.",
                        exc.limit) % numbers
    return gettext("Your plan's limit is reached.")
