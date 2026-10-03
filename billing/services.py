"""What a subscription change means, separated from how Stripe said it.

Two halves, and the boundary between them is the point of this module.

**Going out** - `start_checkout` and `start_portal` hand a person to a page
Stripe hosts. No card number ever reaches this application, which is the whole
argument for hosted Checkout: no PCI scope, no dunning code, no custom billing
UI to get wrong.

**Coming back** - `apply_event` is the *only* writer of `Subscription.status`
and `Subscription.plan`. Not the success redirect, which a person can forge by
typing the URL, and which arrives before the payment has necessarily settled.
A webhook that Stripe signed is the only statement about money this
application believes.

Three decisions worth knowing before reading the code:

**Every subscription carries our user id in its metadata.** Stripe does not
guarantee event order, so `customer.subscription.created` can arrive before
`checkout.session.completed` - and a handler that learned the customer id only
from the checkout session would drop the first one. Writing `user_id` into
`subscription_data.metadata` at checkout makes every later event
self-identifying, and ordering stops mattering.

**`past_due` keeps the plan and says so loudly.** Stripe retries a failed card
for about two weeks; cutting someone off on the first failure would punish an
expired card more harshly than a cancellation. What the phase forbids is
leaving them on a paid plan *silently*, so the entitlement stays and
`Subscription.needs_attention` puts a banner on the page. The downgrade happens
on `customer.subscription.deleted`, which is what Stripe sends when the retries
are exhausted.

**A plan with no `stripe_price_id` cannot be bought.** That is the state of
every plan until somebody creates the products in the Stripe dashboard, and
`purchasable_plans()` returns nothing rather than offering a button that fails.
"""

import logging

from django.conf import settings
from django.db import transaction

from billing import stripe_api
from billing.models import Plan, StripeEvent, Subscription

logger = logging.getLogger(__name__)

#: The events this application acts on. Anything else Stripe sends is recorded
#: and ignored - an endpoint that raises on an event type somebody enabled in
#: the dashboard is an endpoint that Stripe eventually disables.
HANDLED = (
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
)


#: Checkout parameters Stripe refuses on a Managed Payments session, because
#: Stripe as the merchant of record controls tax, payment methods, the
#: customer's billing details and the invoices itself. None of them is sent
#: today; the list exists so a test keeps it that way.
#: https://docs.stripe.com/payments/managed-payments/update-checkout
MANAGED_PAYMENTS_FORBIDDEN = (
    "adaptive_pricing",
    "automatic_tax",
    "tax_id_collection",
    "payment_method_configuration",
    "payment_method_types",
    "customer_update",
    "shipping_address_collection",
    "shipping_options",
    "invoice_creation",
)
MANAGED_PAYMENTS_FORBIDDEN_IN_SUBSCRIPTION_DATA = (
    "default_tax_rates",
    "application_fee_percent",
    "on_behalf_of",
    "transfer_data",
    "invoice_settings",
)


class BillingNotConfigured(Exception):
    """No Stripe keys, or the plan has no price. Nothing to charge against."""


class AlreadySubscribed(Exception):
    """The person is already paying; a plan change belongs in the portal.

    Hosted Checkout in subscription mode always creates a *new* subscription.
    Sending a paying customer through it again would bill them twice a month,
    and the local row - which holds one subscription - would then track
    whichever event arrived last.
    """


# --- going out --------------------------------------------------------------

def purchasable_plans():
    """Plans a person could actually buy right now.

    Empty until somebody creates the products in Stripe and fills in
    `stripe_price_id`, which is deliberately visible: the upgrade page says
    "not configured yet" rather than showing three buttons that 500.
    """
    if not stripe_api.is_configured():
        return Plan.objects.none()
    return (Plan.objects
            .filter(is_active=True, is_default=False)
            .exclude(stripe_price_id="")
            .order_by("price_chf_cents"))


def start_checkout(user, plan: Plan, *, success_url: str, cancel_url: str,
                   terms_url: str, currency: str | None = None) -> str:
    """A hosted Checkout page for this user and plan. Returns its URL.

    The user id travels twice on purpose: as `client_reference_id`, which comes
    back on the checkout session, and inside `subscription_data.metadata`,
    which comes back on **every** later subscription event. The second is what
    makes the webhook order-independent.

    With `STRIPE_MANAGED_PAYMENTS` on, Stripe ("Sold through Link") sells the
    plan as merchant of record and deals with the VAT. The subscription and
    its invoices still live in our Stripe account, so the webhook handling
    below does not change - the launch runbook proves that in test mode.

    With `LOCAL_PRICES` on, `currency` is the one the plans page showed and the
    session is fixed to it, so Checkout charges the same round number (one
    Stripe price carries chf, eur and usd as `currency_options`; a fixed
    currency wins over the visitor's location and over Adaptive Pricing).
    Off, nothing is sent and Checkout behaves as before.
    """
    if not stripe_api.is_configured() or not plan.stripe_price_id:
        raise BillingNotConfigured(
            "This installation cannot take payments yet: no Stripe key, or the "
            "plan has no price configured."
        )

    subscription = subscription_for(user)
    if subscription.is_live_paid:
        raise AlreadySubscribed(
            f"You are already on {subscription.plan.name}. Switching plans happens in "
            "the billing portal, so that you are never charged for two at once."
        )
    managed = settings.STRIPE_MANAGED_PAYMENTS
    where_to_cancel = ("in the billing portal or at link.com" if managed
                       else "in the billing portal")
    params = {
        "mode": "subscription",
        "line_items": [{"price": plan.stripe_price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(user.pk),
        "subscription_data": {"metadata": {"user_id": str(user.pk),
                                           "plan_slug": plan.slug}},
        # The order is concluded on Stripe's page, so that is where the terms
        # have to be accepted and the button has to say what it does. The
        # checkbox needs a terms URL in Stripe's Dashboard (Settings -> Public
        # details); without one the API refuses the session (launch runbook).
        "submit_type": "subscribe",
        "consent_collection": {"terms_of_service": "required"},
        "custom_text": {"terms_of_service_acceptance": {"message": (
            f"I agree to the [Terms of service]({terms_url}) and ask for the plan to start "
            f"immediately. It renews every month until I cancel it {where_to_cancel}. "
            "I can ask for a full refund within 14 days of my first payment."
        )}},
        # Let a returning customer keep one Stripe customer record rather than
        # accumulating one per upgrade, which is what makes the portal able to
        # show their invoice history.
        **({"customer": subscription.stripe_customer_id}
           if subscription.stripe_customer_id else {"customer_email": user.email}),
        **({"managed_payments": {"enabled": True}} if managed else {}),
        **({"currency": currency} if settings.LOCAL_PRICES and currency else {}),
    }
    return stripe_api.checkout_session(**params).url


def start_portal(user, *, return_url: str) -> str:
    """The hosted Customer Portal: change card, see invoices, cancel.

    Cancelling happens here rather than in this application, which is why there
    is no cancel button of our own. The portal is also where Stripe handles the
    dunning emails, the VAT receipts and the proration arithmetic - all of
    which would otherwise be code in this repository.
    """
    subscription = subscription_for(user)
    if not stripe_api.is_configured() or not subscription.stripe_customer_id:
        raise BillingNotConfigured("This account has never been to Stripe.")
    return stripe_api.portal_session(
        customer=subscription.stripe_customer_id, return_url=return_url
    ).url


def subscription_for(user) -> Subscription:
    """The user's subscription row, created on the free plan if it is missing.

    Every user gets one at signup; this is the belt to that braces, because a
    `Subscription.DoesNotExist` inside a webhook is an exception nobody sees
    until a payment has already been taken.
    """
    try:
        return user.subscription
    except Subscription.DoesNotExist:
        return Subscription.objects.create(
            user=user, plan=Plan.objects.get(is_default=True)
        )


# --- coming back ------------------------------------------------------------

def record_event(event) -> bool:
    """Write the event down. False means we have already seen it.

    **This runs before anything is acted on**, and it is the single most
    commonly missed piece of a Stripe integration. Stripe retries a delivery
    until it gets a 2xx, and a retry of `invoice.paid` that ran twice would
    extend a period twice. `get_or_create` on the unique event id turns "at
    least once" delivery into "exactly once" processing.
    """
    _, created = StripeEvent.objects.get_or_create(
        stripe_event_id=event["id"],
        defaults={"type": event["type"], "payload": dict(event)},
    )
    return created


@transaction.atomic
def apply_event(event) -> str:
    """Act on one verified Stripe event. Returns what it did, for the log.

    The return value is a short phrase rather than a status code because the
    only consumers are a log line and a test: the webhook always answers 200 to
    a signed event it has recorded, whether or not it had anything to do with
    it.
    """
    if not record_event(event):
        return "already seen"

    kind = event["type"]
    if kind not in HANDLED:
        return f"ignored {kind}"

    obj = event["data"]["object"]
    created = _created(event)

    if kind == "checkout.session.completed":
        return _checkout_completed(obj)
    if kind.startswith("customer.subscription."):
        return _subscription_changed(kind, obj, created)
    if kind == "invoice.paid":
        return _mark(obj, Subscription.Status.ACTIVE, "active", created)
    if kind == "invoice.payment_failed":
        return _mark(obj, Subscription.Status.PAST_DUE, "past due", created)
    return f"ignored {kind}"


def _created(event):
    """When Stripe created the event, as an aware datetime, or None."""
    from datetime import UTC, datetime

    stamp = event.get("created")
    return datetime.fromtimestamp(int(stamp), tz=UTC) if stamp else None


def _is_stale(subscription: Subscription, created) -> bool:
    """Older than the newest event already applied to this row.

    Strictly older: Stripe stamps whole seconds, and two events of the same
    second are both news.
    """
    return bool(
        created and subscription.last_event_at and created < subscription.last_event_at
    )


def _note_event(subscription: Subscription, created) -> None:
    if created and (subscription.last_event_at is None or created > subscription.last_event_at):
        subscription.last_event_at = created


def _checkout_completed(session) -> str:
    """Attach the Stripe customer to our user. Nothing about entitlement.

    The plan is deliberately *not* set here. `customer.subscription.created`
    carries the price that was actually bought; a checkout session says what
    was put in the basket. They agree almost always, and "almost" is the whole
    reason to read the one that is authoritative.
    """
    user_id = session.get("client_reference_id")
    if not user_id:
        return "checkout with no user reference"

    subscription = _by_user_id(user_id)
    if subscription is None:
        return f"checkout for unknown user {user_id}"

    subscription.stripe_customer_id = session.get("customer") or ""
    if session.get("subscription"):
        subscription.stripe_subscription_id = session["subscription"]
    subscription.save(update_fields=["stripe_customer_id", "stripe_subscription_id"])
    return "customer attached"


def _subscription_changed(kind: str, obj, created=None) -> str:
    """The authoritative statement about what somebody is entitled to.

    Two guards come before it is believed. **A stale event is ignored**: an
    `updated` retried for days can land after the `deleted` that ended the
    subscription, and applying it would restore a paid plan that no later
    event would ever take away again. **A deletion of some other subscription
    ends nothing here**: the row tracks one subscription, and a second one
    (made in the dashboard, or before checkout refused a paying customer) must
    not downgrade somebody whose current subscription is still being billed.
    """
    subscription = _find(obj)
    if subscription is None:
        return "no local subscription for this customer"

    if _is_stale(subscription, created):
        logger.warning("stale %s for subscription %s ignored", kind, obj.get("id"))
        return "stale event ignored"

    if kind == "customer.subscription.deleted":
        current = subscription.stripe_subscription_id
        if current and obj.get("id") and obj.get("id") != current:
            logger.warning("deletion of %s is not the tracked %s", obj.get("id"), current)
            return "a different subscription ended; this one is unchanged"
        subscription.plan = Plan.objects.get(is_default=True)
        subscription.status = Subscription.Status.CANCELED
        subscription.cancel_at_period_end = False
        subscription.stripe_subscription_id = ""
        _note_event(subscription, created)
        subscription.save()
        return "downgraded to the free plan"

    plan = _plan_from(obj)
    if plan is not None:
        subscription.plan = plan
    subscription.status = _status_from(obj.get("status", ""))
    subscription.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    subscription.stripe_subscription_id = obj.get("id") or ""
    subscription.stripe_customer_id = (
        obj.get("customer") or subscription.stripe_customer_id
    )
    subscription.current_period_end = _period_end(obj)
    _note_event(subscription, created)
    subscription.save()
    return f"set to {subscription.plan.slug} ({subscription.status})"


def _mark(invoice, status: str, phrase: str, created=None) -> str:
    """Move the status without touching the plan.

    An invoice says whether money arrived. It does not say what somebody
    bought, so it must never change `plan` - that is what let a failed payment
    on a *renewal* silently swap somebody's tier in an earlier draft of this.
    Stale ones are ignored for the same reason as subscription events: a
    retried `payment_failed` must not flag a subscription that has since paid.
    """
    subscription = _find(invoice)
    if subscription is None:
        return "no local subscription for this customer"
    if _is_stale(subscription, created):
        logger.warning("stale invoice event %s ignored", invoice.get("id"))
        return "stale event ignored"
    subscription.status = status
    _note_event(subscription, created)
    subscription.save(update_fields=["status", "last_event_at"])
    return phrase


# --- finding the local row --------------------------------------------------

def _find(obj) -> Subscription | None:
    """Our subscription for a Stripe object, by metadata first.

    Metadata first and customer id second, because metadata is written by us at
    checkout and survives every later event, while the customer id is only on
    our row once `checkout.session.completed` has been processed - which,
    without a guaranteed order, may not have happened yet.
    """
    metadata = obj.get("metadata") or {}
    found = _by_user_id(metadata.get("user_id"))
    if found is not None:
        return found

    customer = obj.get("customer")
    if not customer:
        logger.warning("stripe object %s has no customer", obj.get("id"))
        return None
    return Subscription.objects.filter(stripe_customer_id=customer).first()


def _by_user_id(user_id) -> Subscription | None:
    if not user_id:
        return None
    return Subscription.objects.filter(user_id=user_id).first()


def _plan_from(obj) -> Plan | None:
    """The plan whose price this subscription is actually on."""
    for item in (obj.get("items") or {}).get("data") or []:
        price_id = (item.get("price") or {}).get("id")
        plan = Plan.objects.filter(stripe_price_id=price_id).first() if price_id else None
        if plan is not None:
            return plan

    # Falling back to the slug we wrote at checkout. Weaker - it says what was
    # put in the basket rather than what is being billed - so it is only
    # reached when the price id matches no plan we know, which means somebody
    # changed the products in the dashboard without telling the database.
    slug = (obj.get("metadata") or {}).get("plan_slug")
    if slug:
        logger.warning("stripe price not recognised; falling back to slug %s", slug)
        return Plan.objects.filter(slug=slug).first()
    return None


#: Stripe's subscription statuses, mapped onto the three this application has.
#: `trialing` counts as active because a trial is access. `incomplete` and
#: `unpaid` are not past due - nothing was ever successfully charged - but they
#: are the same entitlement question, and the banner says the same thing.
_STATUS = {
    "active": Subscription.Status.ACTIVE,
    "trialing": Subscription.Status.ACTIVE,
    "past_due": Subscription.Status.PAST_DUE,
    "incomplete": Subscription.Status.PAST_DUE,
    "unpaid": Subscription.Status.PAST_DUE,
    "canceled": Subscription.Status.CANCELED,
    "incomplete_expired": Subscription.Status.CANCELED,
}


def _status_from(stripe_status: str) -> str:
    return _STATUS.get(stripe_status, Subscription.Status.ACTIVE)


def _period_end(obj):
    """When the current period ends, as an aware datetime.

    Stripe moved this off the subscription and onto each item, so both places
    are read: `current_period_end` at the top level for older API versions, and
    the first item's for current ones.
    """
    from datetime import UTC, datetime

    stamp = obj.get("current_period_end")
    if stamp is None:
        for item in (obj.get("items") or {}).get("data") or []:
            stamp = item.get("current_period_end")
            if stamp:
                break
    if not stamp:
        return None
    return datetime.fromtimestamp(int(stamp), tz=UTC)
