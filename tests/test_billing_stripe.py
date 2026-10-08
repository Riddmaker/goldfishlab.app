"""Phase 6 §3: paid plans, and the webhook that is the only source of truth.

Every test here builds its own Stripe event as a dictionary. Nothing talks to
Stripe and nothing needs a real API key. **Nothing about the webhook path is
faked either**: a webhook test signs its body with a test secret through the
library's own `WebhookSignature.generate_signature_header` and posts it, so the
real `construct_event` verifies and parses it.

That is the lesson of trap 45. This file used to replace `construct_event` with
`json.loads`, on the reasoning that it is "exactly what the real one does once
the HMAC checks out". It was not: the real one returned a `stripe.Event`, which
in `stripe==15` is not a dict, and every real webhook would have answered 500
while all of these stayed green.

The four things the phase document asks to be proved, in its words:

1. **"Webhook replay (same event twice) changes nothing the second time."**
   Stripe retries until it gets a 2xx. This is the single most commonly missed
   piece of a Stripe integration, so it is the first section below.
2. **"Subscription downgrade at period end correctly reduces quota."** Asserted
   through `quotas.check`, not by reading a field, because the field is only
   interesting if the thing that enforces limits agrees with it.
3. **"A failed payment does not silently leave a user on a paid plan."** The
   emphasis is on *silently*: the plan survives the retry window on purpose and
   the account is flagged. `test_a_failed_payment_is_never_silent` pins both
   halves, and the cancellation that follows exhausted retries is what actually
   downgrades.
4. **"The webhook is the only writer of Subscription.status."** So the success
   page grants nothing at all, and there is a test that types its URL.
"""

import json
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from billing import quotas, services, stripe_api
from billing.models import Plan, StripeEvent, Subscription, UsageRecord

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "pw-for-test-only"

CUSTOMER = "cus_test_123"
SUBSCRIPTION = "sub_test_123"
PRICE = "price_koi_test"


@pytest.fixture
def paid_plan():
    """The Koi tier, with a price id as if somebody had made it in Stripe."""
    plan = Plan.objects.get(slug="koi")
    plan.stripe_price_id = PRICE
    plan.save(update_fields=["stripe_price_id"])
    return plan


@pytest.fixture
def user():
    return User.objects.create_user(email="payer@example.com", password=PASSWORD)


@pytest.fixture
def subscription(user):
    return services.subscription_for(user)


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


WEBHOOK_SECRET = "whsec_test_only_not_a_real_secret"


@pytest.fixture
def webhook_secret(settings):
    """An installation with keys, so the webhook is open for business."""
    settings.STRIPE_SECRET_KEY = "sk_test_not_a_real_key"
    settings.STRIPE_WEBHOOK_SECRET = WEBHOOK_SECRET
    return WEBHOOK_SECRET


def post_signed(client, event: dict):
    """Post an event exactly as Stripe would: the raw body plus its HMAC."""
    import stripe

    body = json.dumps(event)
    header = stripe.WebhookSignature.generate_signature_header(body, WEBHOOK_SECRET)
    return client.post(
        reverse("billing:webhook"),
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": header},
    )


# --- building events --------------------------------------------------------

def subscription_event(kind, user, *, status="active", price=PRICE,
                       cancel_at_period_end=False, event_id=None, period_end=None,
                       created=None, subscription_id=SUBSCRIPTION):
    """One `customer.subscription.*` event, shaped like Stripe's.

    The user id lives in `metadata` because that is what this application puts
    there at checkout, and it is what makes the handler independent of the
    order events arrive in.
    """
    event = {
        "id": event_id or f"evt_{kind}_{user.pk}",
        "type": kind,
        "data": {"object": {
            "id": subscription_id,
            "customer": CUSTOMER,
            "status": status,
            "cancel_at_period_end": cancel_at_period_end,
            "metadata": {"user_id": str(user.pk), "plan_slug": "koi"},
            "items": {"data": [{"price": {"id": price},
                                "current_period_end": period_end or 1800000000}]},
        }},
    }
    if created is not None:
        event["created"] = created
    return event


def invoice_event(kind, *, event_id=None, customer=CUSTOMER):
    return {
        "id": event_id or f"evt_{kind}",
        "type": kind,
        "data": {"object": {"id": "in_test", "customer": customer, "metadata": {}}},
    }


# --- 1. the same event twice ------------------------------------------------

def test_a_replayed_event_changes_nothing_the_second_time(subscription, paid_plan, user):
    """Stripe retries until it gets a 2xx. Both deliveries must land once."""
    event = subscription_event("customer.subscription.created", user)

    assert services.apply_event(event) == "set to koi (active)"
    subscription.refresh_from_db()
    first = (subscription.plan_id, subscription.status)

    assert services.apply_event(event) == "already seen"
    subscription.refresh_from_db()
    assert (subscription.plan_id, subscription.status) == first
    assert StripeEvent.objects.filter(stripe_event_id=event["id"]).count() == 1


def test_the_event_is_recorded_before_it_is_acted_on(subscription, paid_plan, user):
    """Recording second would let a crash mid-handler replay the whole thing."""
    event = subscription_event("customer.subscription.created", user)
    services.apply_event(event)
    stored = StripeEvent.objects.get(stripe_event_id=event["id"])
    assert stored.type == "customer.subscription.created"
    assert stored.payload["data"]["object"]["customer"] == CUSTOMER


def test_two_different_events_both_land(subscription, paid_plan, user):
    services.apply_event(subscription_event(
        "customer.subscription.created", user, event_id="evt_one"))
    services.apply_event(subscription_event(
        "customer.subscription.updated", user, event_id="evt_two",
        cancel_at_period_end=True))

    subscription.refresh_from_db()
    assert subscription.cancel_at_period_end
    assert StripeEvent.objects.count() == 2


# --- 2. what a plan change does to quota ------------------------------------

def test_paying_raises_the_limits_the_application_enforces(subscription, paid_plan, user):
    """Through `quotas`, not by reading a field. The field only matters if the
    thing that refuses work agrees with it."""
    free = quotas.plan_for(user)
    assert free.max_turns == 9

    services.apply_event(subscription_event("customer.subscription.created", user))

    user.refresh_from_db()
    assert quotas.plan_for(user).max_turns == 12
    assert quotas.plan_for(user).max_runs_per_month == 300


def test_a_cancelled_subscription_reduces_the_quota_again(subscription, paid_plan, user):
    """The downgrade the phase asks about, end to end."""
    services.apply_event(subscription_event("customer.subscription.created", user))
    user.refresh_from_db()
    assert quotas.plan_for(user).max_turns == 12

    services.apply_event(subscription_event(
        "customer.subscription.deleted", user, event_id="evt_gone"))

    user.refresh_from_db()
    subscription.refresh_from_db()
    assert subscription.plan.is_default
    assert subscription.status == Subscription.Status.CANCELED
    assert quotas.plan_for(user).max_turns == 9


def test_cancelling_at_period_end_changes_nothing_yet(subscription, paid_plan, user):
    """The person paid for this month. They keep this month."""
    services.apply_event(subscription_event("customer.subscription.created", user))
    services.apply_event(subscription_event(
        "customer.subscription.updated", user,
        cancel_at_period_end=True, event_id="evt_will_end"))

    user.refresh_from_db()
    subscription.refresh_from_db()
    assert subscription.cancel_at_period_end
    assert subscription.ends_soon
    assert quotas.plan_for(user).max_turns == 12, "cancelling is not the same as ending"


def test_a_cancelled_status_never_entitles_anybody(subscription, paid_plan, user):
    """A second lock on the same door.

    The webhook already sets the plan back to free on deletion. If the two ever
    disagree - a hand-edited row, a half-applied event - the cautious answer has
    to be the one that does not hand out a paid plan.
    """
    subscription.plan = paid_plan
    subscription.status = Subscription.Status.CANCELED
    subscription.save()

    user.refresh_from_db()
    assert quotas.plan_for(user).is_default


def test_a_lower_limit_is_enforced_and_not_merely_displayed(subscription, paid_plan, user):
    """Use up the free allowance, then check the refusal is real."""
    UsageRecord.objects.create(
        user=user, period_start=quotas.period_start(),
        metric=UsageRecord.Metric.RUNS_STARTED, amount=20,
    )
    with pytest.raises(quotas.QuotaExceeded):
        quotas.check(user, UsageRecord.Metric.RUNS_STARTED)

    services.apply_event(subscription_event("customer.subscription.created", user))
    user.refresh_from_db()
    assert quotas.check(user, UsageRecord.Metric.RUNS_STARTED).allowed


# --- 3. a failed payment ----------------------------------------------------

def test_a_failed_payment_is_never_silent(subscription, paid_plan, user):
    """The plan survives the retry window; the account is flagged.

    Stripe retries a card for about two weeks. Cutting somebody off on the
    first failure would punish an expired card more harshly than a
    cancellation, so the entitlement stays - and `needs_attention` is what
    makes it not silent. The template turns that into a banner.
    """
    services.apply_event(subscription_event("customer.subscription.created", user))
    services.apply_event(invoice_event("invoice.payment_failed"))

    subscription.refresh_from_db()
    assert subscription.status == Subscription.Status.PAST_DUE
    assert subscription.needs_attention
    assert subscription.plan.slug == "koi"


def test_a_failed_payment_that_is_never_fixed_ends_in_a_downgrade(
        subscription, paid_plan, user):
    """The other half. Stripe gives up and sends a deletion; that is the cliff."""
    services.apply_event(subscription_event("customer.subscription.created", user))
    services.apply_event(invoice_event("invoice.payment_failed"))
    services.apply_event(subscription_event(
        "customer.subscription.deleted", user, event_id="evt_given_up"))

    user.refresh_from_db()
    assert quotas.plan_for(user).is_default


def test_a_paid_invoice_clears_the_flag(subscription, paid_plan, user):
    services.apply_event(subscription_event("customer.subscription.created", user))
    services.apply_event(invoice_event("invoice.payment_failed"))
    services.apply_event(invoice_event("invoice.paid", event_id="evt_paid_at_last"))

    subscription.refresh_from_db()
    assert subscription.status == Subscription.Status.ACTIVE
    assert not subscription.needs_attention


def test_an_invoice_never_changes_which_plan_somebody_is_on(
        subscription, paid_plan, user):
    """An invoice says whether money arrived, never what was bought.

    Letting it touch `plan` is how a failed payment on a renewal could swap
    somebody's tier for whatever the fallback happened to be.
    """
    services.apply_event(subscription_event("customer.subscription.created", user))
    subscription.refresh_from_db()
    before = subscription.plan_id
    assert not subscription.plan.is_default, "the paid plan is what we are guarding"

    services.apply_event(invoice_event("invoice.payment_failed"))
    subscription.refresh_from_db()
    assert subscription.plan_id == before


# --- 4. the webhook is the only writer --------------------------------------

def test_the_success_page_grants_nothing(signed_in, subscription, paid_plan):
    """It is reachable by typing the URL, so it had better not be a payment."""
    response = signed_in.get(reverse("billing:done"))
    assert response.status_code == 200

    subscription.refresh_from_db()
    assert subscription.plan.is_default
    assert b"your plan changes within a few seconds" in response.content


def test_an_unsigned_webhook_is_refused(client, settings):
    """The real signature path, with no monkeypatching anywhere near it.

    This endpoint is public, unauthenticated and CSRF-exempt. The HMAC is the
    only thing that makes anything it says believable.
    """
    settings.STRIPE_SECRET_KEY = "sk_test_not_a_real_key"
    settings.STRIPE_WEBHOOK_SECRET = "whsec_not_a_real_secret"

    response = client.post(
        reverse("billing:webhook"),
        data=json.dumps({"id": "evt_forged", "type": "invoice.paid"}),
        content_type="application/json",
        headers={"Stripe-Signature": "t=1,v1=nonsense"},
    )
    assert response.status_code == 400
    assert not StripeEvent.objects.exists()


def test_a_webhook_to_an_unconfigured_installation_is_refused(client):
    """No keys means no webhook secret, so anything arriving here is misdirected."""
    response = client.post(reverse("billing:webhook"), data="{}",
                           content_type="application/json")
    assert response.status_code == 400


def test_a_signed_event_is_accepted_and_applied(client, subscription, paid_plan,
                                                user, webhook_secret):
    """The real path end to end: signature, parsing, recording, applying."""
    response = post_signed(client, subscription_event("customer.subscription.created", user))
    assert response.status_code == 200
    subscription.refresh_from_db()
    assert subscription.plan.slug == "koi"
    assert StripeEvent.objects.get().payload["type"] == "customer.subscription.created"


def test_the_parsed_event_is_a_plain_dict(webhook_secret):
    """Trap 45, pinned where it lives rather than where it surfaced."""
    import stripe

    body = json.dumps({"id": "evt_1", "type": "invoice.paid", "data": {"object": {}}})
    header = stripe.WebhookSignature.generate_signature_header(body, WEBHOOK_SECRET)
    event = stripe_api.construct_event(body.encode(), header)
    assert type(event) is dict
    assert type(event["data"]["object"]) is dict


def test_a_replayed_old_signature_is_refused(client, webhook_secret):
    """`verify_header` only checks the timestamp when it is given a tolerance."""
    import time

    import stripe

    body = json.dumps({"id": "evt_old", "type": "invoice.paid", "data": {"object": {}}})
    header = stripe.WebhookSignature.generate_signature_header(
        body, WEBHOOK_SECRET, timestamp=int(time.time()) - 3600
    )
    response = client.post(reverse("billing:webhook"), data=body,
                           content_type="application/json",
                           headers={"Stripe-Signature": header})
    assert response.status_code == 400
    assert not StripeEvent.objects.exists()


def test_an_event_type_we_do_not_handle_still_answers_200(client, user, webhook_secret):
    """Anything but a 2xx makes Stripe retry for days.

    Somebody enabling `charge.dispute.created` in the dashboard must not turn
    into a retry storm against an endpoint that has nothing to say about it.
    """
    response = post_signed(client, {"id": "evt_odd", "type": "charge.dispute.created",
                                    "data": {"object": {}}})
    assert response.status_code == 200
    assert StripeEvent.objects.filter(stripe_event_id="evt_odd").exists()


# --- order independence -----------------------------------------------------

def test_a_subscription_event_arriving_first_still_finds_its_user(
        subscription, paid_plan, user):
    """Stripe does not guarantee order.

    A handler that learned the customer id only from `checkout.session.completed`
    would drop this one. The user id is in the subscription's metadata for
    exactly this reason.
    """
    assert not subscription.stripe_customer_id

    services.apply_event(subscription_event("customer.subscription.created", user))

    subscription.refresh_from_db()
    assert subscription.plan.slug == "koi"
    assert subscription.stripe_customer_id == CUSTOMER


def test_a_checkout_session_attaches_the_customer_and_nothing_else(
        subscription, paid_plan, user):
    """It says what went in the basket. The subscription event says what is billed."""
    services.apply_event({
        "id": "evt_checkout",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test", "customer": CUSTOMER,
                            "subscription": SUBSCRIPTION,
                            "client_reference_id": str(user.pk)}},
    })

    subscription.refresh_from_db()
    assert subscription.stripe_customer_id == CUSTOMER
    assert subscription.stripe_subscription_id == SUBSCRIPTION
    assert subscription.plan.is_default, "entitlement comes from the subscription event"


def test_an_event_for_a_customer_we_do_not_know_is_survivable(paid_plan):
    """A test-mode endpoint pointed at a live database, usually.

    It must not raise: a 500 here makes Stripe retry an event that retrying
    cannot fix.
    """
    outcome = services.apply_event({
        "id": "evt_stranger",
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_x", "customer": "cus_nobody",
                            "status": "active", "metadata": {}, "items": {"data": []}}},
    })
    assert "no local subscription" in outcome


# --- the plans page ---------------------------------------------------------

def test_the_plans_page_shows_the_tiers_and_this_months_usage(signed_in, user):
    response = signed_in.get(reverse("billing:plans"))
    assert response.status_code == 200
    body = response.content.decode()
    for name in ("Goldfish", "Koi", "Kraken"):
        assert name in body
    assert "Simulations started" in body


def test_an_installation_with_no_keys_offers_nothing_to_buy(signed_in):
    """True of development, the test suite and every screenshot run.

    An upgrade button that 500s is worse than an upgrade button that is absent,
    and the page says which of the two is happening.
    """
    body = signed_in.get(reverse("billing:plans")).content.decode()
    assert "not switched on" in body
    assert reverse("billing:checkout", args=["koi"]) not in body


def test_nothing_is_purchasable_without_a_price_id(settings, monkeypatch):
    """A plan nobody has made in Stripe cannot be bought, even with keys set."""
    monkeypatch.setattr(stripe_api, "is_configured", lambda: True)
    assert not services.purchasable_plans().exists()

    plan = Plan.objects.get(slug="kraken")
    plan.stripe_price_id = "price_kraken"
    plan.save(update_fields=["stripe_price_id"])
    assert [p.slug for p in services.purchasable_plans()] == ["kraken"]


def test_checkout_without_configuration_says_so_rather_than_breaking(signed_in):
    response = signed_in.post(reverse("billing:checkout", args=["koi"]))
    assert response.status_code == 302
    assert response["Location"] == reverse("billing:plans")


def test_the_free_plan_cannot_be_checked_out(signed_in):
    assert signed_in.post(reverse("billing:checkout", args=["free"])).status_code == 400


def test_a_plan_that_does_not_exist_is_refused(signed_in):
    assert signed_in.post(
        reverse("billing:checkout", args=["mythic"])).status_code == 400


def test_billing_pages_need_a_signed_in_user(client):
    for name in ("billing:plans", "billing:done"):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert "login" in response["Location"]


def test_the_portal_needs_a_stripe_customer(signed_in):
    """Somebody who has never paid has nothing to manage."""
    response = signed_in.post(reverse("billing:portal"))
    assert response.status_code == 302
    assert response["Location"] == reverse("billing:plans")


# --- what a refusal looks like ----------------------------------------------

def test_a_used_up_quota_lands_on_a_page_that_can_fix_it(signed_in, user):
    """HTTP 402, and a link to the tier that has more.

    A quota refusal used to be a red message on the deck page, which told
    somebody what had happened and nothing about what to do next. `402 Payment
    Required` is the one status code that means exactly this.
    """
    import uuid

    from cards.models import OracleCard
    from decks.models import Deck, DeckCard

    deck = Deck.objects.create(owner=user, name="Something to refuse")
    # One card, because the view refuses an empty deck before it ever reaches
    # the quota - and it is the quota refusal being tested here.
    card = OracleCard.objects.create(
        oracle_id=uuid.uuid4(), name="Swamp", front_name="Swamp",
        search_name="swamp", type_line="Basic Land — Swamp",
    )
    DeckCard.objects.create(deck=deck, oracle_card=card, quantity=1)
    UsageRecord.objects.create(
        user=user, period_start=quotas.period_start(),
        metric=UsageRecord.Metric.RUNS_STARTED, amount=20,
    )

    response = signed_in.post(
        reverse("simulations:create", args=[deck.id]),
        {"games": 1000, "turns": 6, "on_the_play": 1},
    )

    assert response.status_code == 402
    body = response.content.decode()
    assert reverse("billing:plans") in body
    assert "Nothing was started and nothing was charged" in body


# --- a second subscription, and events out of order --------------------------

def test_a_paying_customer_is_not_sent_through_checkout_again(signed_in, subscription,
                                                             paid_plan, user, monkeypatch):
    """Checkout always starts a NEW subscription: a second one bills twice."""
    monkeypatch.setattr(stripe_api, "is_configured", lambda: True)
    kraken = Plan.objects.get(slug="kraken")
    kraken.stripe_price_id = "price_kraken_test"
    kraken.save(update_fields=["stripe_price_id"])
    services.apply_event(subscription_event("customer.subscription.created", user))

    def _no_checkout(**params):
        raise AssertionError("a second checkout session was created")

    monkeypatch.setattr(stripe_api, "checkout_session", _no_checkout)
    response = signed_in.post(reverse("billing:checkout", args=["kraken"]))

    assert response.status_code == 302
    assert response["Location"] == reverse("billing:plans")
    page = signed_in.get(reverse("billing:plans")).content.decode()
    assert reverse("billing:checkout", args=["kraken"]) not in page
    assert "billing portal" in page


def test_checkout_asks_for_the_terms_and_says_it_subscribes(signed_in, subscription,
                                                            paid_plan, monkeypatch):
    """The contract is concluded on Stripe's page, so the terms are accepted there."""
    monkeypatch.setattr(stripe_api, "is_configured", lambda: True)
    sent = {}

    def _checkout(**params):
        sent.update(params)
        return SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_test")

    monkeypatch.setattr(stripe_api, "checkout_session", _checkout)
    response = signed_in.post(reverse("billing:checkout", args=["koi"]))

    assert response.status_code == 302
    assert sent["submit_type"] == "subscribe"
    assert sent["consent_collection"] == {"terms_of_service": "required"}
    message = sent["custom_text"]["terms_of_service_acceptance"]["message"]
    assert "http://testserver" + reverse("terms") in message
    assert "14 days" in message


def _capture_checkout(monkeypatch) -> dict:
    """Replace the Stripe call with one that records what would have been sent."""
    monkeypatch.setattr(stripe_api, "is_configured", lambda: True)
    sent = {}

    def _checkout(**params):
        sent.update(params)
        return SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_test")

    monkeypatch.setattr(stripe_api, "checkout_session", _checkout)
    return sent


@pytest.mark.parametrize("switched_on", [False, True])
def test_managed_payments_is_asked_for_only_when_switched_on(
        signed_in, subscription, paid_plan, monkeypatch, settings, switched_on):
    """Stripe refuses the parameter on an account it has not approved.

    So it must never leave the building while the setting is off - that would
    break every checkout between the go-live and Stripe's eligibility review.
    """
    settings.STRIPE_MANAGED_PAYMENTS = switched_on
    sent = _capture_checkout(monkeypatch)

    signed_in.post(reverse("billing:checkout", args=["koi"]))

    if switched_on:
        assert sent["managed_payments"] == {"enabled": True}
    else:
        assert "managed_payments" not in sent
    message = sent["custom_text"]["terms_of_service_acceptance"]["message"]
    assert ("link.com" in message) is switched_on
    assert "14 days" in message


@pytest.mark.parametrize("returning", [False, True])
def test_a_managed_payments_session_sends_nothing_stripe_forbids(
        signed_in, subscription, paid_plan, monkeypatch, settings, returning):
    """The list is Stripe's; a parameter from it makes the session fail outright."""
    settings.STRIPE_MANAGED_PAYMENTS = True
    if returning:
        subscription.stripe_customer_id = CUSTOMER
        subscription.save(update_fields=["stripe_customer_id"])
    sent = _capture_checkout(monkeypatch)

    signed_in.post(reverse("billing:checkout", args=["koi"]))

    assert sent["managed_payments"] == {"enabled": True}
    assert not set(sent) & set(services.MANAGED_PAYMENTS_FORBIDDEN)
    assert not (set(sent["subscription_data"])
                & set(services.MANAGED_PAYMENTS_FORBIDDEN_IN_SUBSCRIPTION_DATA))
    # The metadata is what makes every later webhook find its user; Managed
    # Payments must not cost it.
    assert sent["subscription_data"]["metadata"]["user_id"] == str(subscription.user.pk)


@pytest.mark.parametrize("switched_on", [False, True])
def test_the_plans_page_says_who_sells_the_plan(signed_in, paid_plan, monkeypatch,
                                                settings, switched_on):
    settings.STRIPE_MANAGED_PAYMENTS = switched_on
    monkeypatch.setattr(stripe_api, "is_configured", lambda: True)

    body = signed_in.get(reverse("billing:plans")).content.decode()

    assert ("sold through Link" in body) is switched_on


def test_the_end_of_some_other_subscription_ends_nothing_here(subscription, paid_plan, user):
    services.apply_event(subscription_event("customer.subscription.created", user))

    outcome = services.apply_event(subscription_event(
        "customer.subscription.deleted", user, subscription_id="sub_some_other_one",
        event_id="evt_other_deleted",
    ))

    subscription.refresh_from_db()
    assert "different subscription" in outcome
    assert subscription.plan.slug == "koi"


def test_a_stale_update_cannot_bring_a_cancelled_plan_back(subscription, paid_plan, user):
    """Stripe retries a failed delivery for days and does not order them."""
    services.apply_event(subscription_event(
        "customer.subscription.created", user, created=1_700_000_000))
    services.apply_event(subscription_event(
        "customer.subscription.deleted", user, created=1_700_000_300))

    outcome = services.apply_event(subscription_event(
        "customer.subscription.updated", user, created=1_700_000_100,
        event_id="evt_late_update",
    ))

    subscription.refresh_from_db()
    assert outcome == "stale event ignored"
    assert subscription.plan.is_default
    assert quotas.plan_for(user).is_default


def test_events_in_order_still_apply(subscription, paid_plan, user):
    services.apply_event(subscription_event(
        "customer.subscription.created", user, created=1_700_000_000))
    services.apply_event(subscription_event(
        "customer.subscription.updated", user, created=1_700_000_100,
        cancel_at_period_end=True,
    ))
    subscription.refresh_from_db()
    assert subscription.cancel_at_period_end
    assert subscription.last_event_at.timestamp() == 1_700_000_100


def test_a_late_payment_failure_does_not_flag_a_paid_subscription(subscription, paid_plan,
                                                                   user):
    services.apply_event(subscription_event(
        "customer.subscription.created", user, created=1_700_000_000))
    paid = invoice_event("invoice.paid", event_id="evt_paid_now")
    paid["created"] = 1_700_000_500
    services.apply_event(paid)

    late = invoice_event("invoice.payment_failed", event_id="evt_failed_earlier")
    late["created"] = 1_700_000_200
    services.apply_event(late)

    subscription.refresh_from_db()
    assert subscription.status == Subscription.Status.ACTIVE
