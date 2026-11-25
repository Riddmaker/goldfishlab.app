"""`manage.py stripe_check` (B-18): is Stripe set up as the code expects?

Stripe is replaced at `stripe_api` (price, webhook_endpoints), the one module
that talks to it. The seeded plans are Koi (CHF 4 / 36) and Kraken (CHF 9 / 84).
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from billing import readiness, services, stripe_api
from billing.models import Plan

pytestmark = pytest.mark.django_db

SITE = "https://goldfishlab.app"
KEY = "sk_test_" + "x" * 24
AMOUNTS = {"price_koi_m": ("month", 400), "price_koi_y": ("year", 3600),
           "price_kraken_m": ("month", 900), "price_kraken_y": ("year", 8400)}


def _price(price_id, **change):
    interval, cents = AMOUNTS[price_id]
    price = {"id": price_id, "active": True, "currency": "chf", "unit_amount": cents,
             "recurring": {"interval": interval}, "livemode": False,
             "currency_options": {c: {"unit_amount": cents} for c in ("chf", "eur", "usd")},
             "product": {"id": "prod_1", "active": True, "tax_code": "txcd_10103001"}}
    price.update(change)
    return price


@pytest.fixture
def stripe(settings, monkeypatch):
    """A Stripe account set up right; tests break one thing at a time."""
    settings.STRIPE_SECRET_KEY = KEY
    settings.STRIPE_WEBHOOK_SECRET = "whsec_" + "y" * 24
    settings.STRIPE_MANAGED_PAYMENTS = False
    settings.LOCAL_PRICES = False
    settings.SITE_URL = SITE
    for slug in ("koi", "kraken"):
        Plan.objects.filter(slug=slug).update(
            stripe_price_id=f"price_{slug}_m", stripe_annual_price_id=f"price_{slug}_y")
    account = {"prices": {pid: _price(pid) for pid in AMOUNTS},
               "endpoints": [{"url": f"{SITE}/billing/webhook/", "status": "enabled",
                              "enabled_events": list(services.HANDLED)}]}

    def price(price_id):
        if price_id not in account["prices"]:
            raise stripe_api.StripeError("No such price")
        return account["prices"][price_id]

    monkeypatch.setattr(stripe_api, "price", price)
    monkeypatch.setattr(stripe_api, "webhook_endpoints", lambda: account["endpoints"])
    return account


def _levels():
    return {(f.level, f.text) for f in readiness.findings()}


def _fails():
    return [text for level, text in _levels() if level == "fail"]


def test_a_right_setup_passes_and_names_no_secret(stripe):
    out = StringIO()
    call_command("stripe_check", stdout=out)

    text = out.getvalue()
    assert "FAIL" not in text and "WARN" not in text
    assert "key mode: test" in text
    assert "koi (month): 4 chf" in text and "kraken (year): 84 chf" in text
    assert "sends all 6 events" in text
    assert KEY not in text and "whsec_" not in text


def test_without_a_key_it_says_so_and_fails(settings):
    settings.STRIPE_SECRET_KEY = ""

    with pytest.raises(CommandError, match="1 finding"):
        call_command("stripe_check", stdout=StringIO())


def test_a_missing_webhook_secret_fails(stripe, settings):
    settings.STRIPE_WEBHOOK_SECRET = ""

    assert any("STRIPE_WEBHOOK_SECRET" in text for text in _fails())


def test_a_plan_without_a_price_id_fails(stripe):
    Plan.objects.filter(slug="kraken").update(stripe_annual_price_id="")

    assert _fails() == ["kraken (year): no Stripe price id in the admin (stripe_annual_price_id)"]


def test_a_price_stripe_does_not_know_fails(stripe):
    del stripe["prices"]["price_koi_m"]

    assert _fails() == ["koi (month): Stripe does not know price_koi_m (StripeError)"]


@pytest.mark.parametrize(("change", "says"), [
    ({"active": False}, "archived"),
    ({"recurring": {"interval": "year"}}, "bills every year, not every month"),
    ({"recurring": None}, "bills every once"),
    ({"unit_amount": 500}, "charges 500 chf, the plan shows 400 chf"),
    ({"currency": "eur"}, "charges 400 eur"),
    ({"livemode": True}, "not in test mode"),
])
def test_a_price_that_does_not_match_the_plan_fails(stripe, change, says):
    stripe["prices"]["price_koi_m"] = _price("price_koi_m", **change)

    fails = _fails()
    assert len(fails) == 1 and fails[0].startswith("koi (month)") and says in fails[0]


def test_a_missing_tax_code_warns_and_fails_under_managed_payments(stripe, settings):
    stripe["prices"]["price_koi_m"]["product"]["tax_code"] = None
    says = "koi (month): the product has no tax code (Managed Payments needs one)"
    assert ("warn", says) in _levels()

    settings.STRIPE_MANAGED_PAYMENTS = True
    assert _fails() == [says]


def test_local_prices_need_the_same_amount_in_each_currency(stripe, settings):
    settings.LOCAL_PRICES = True
    assert _fails() == []

    del stripe["prices"]["price_kraken_y"]["currency_options"]["eur"]
    assert _fails() == ["kraken (year): no eur option of 8400 on the price"]


def test_no_webhook_for_this_site_fails(stripe):
    stripe["endpoints"][0]["url"] = "https://example.com/billing/webhook/"

    assert _fails() == [f"no webhook endpoint for {SITE}/billing/webhook/"]


def test_a_webhook_missing_an_event_or_disabled_fails(stripe):
    stripe["endpoints"][0]["enabled_events"] = ["checkout.session.completed", "invoice.paid"]
    assert "does not send: customer.subscription.created" in _fails()[0]

    stripe["endpoints"][0]["status"] = "disabled"
    assert _fails() == [f"webhook {SITE}/billing/webhook/ is disabled"]


def test_a_webhook_on_every_event_passes(stripe):
    stripe["endpoints"][0]["enabled_events"] = ["*"]

    assert _fails() == []


def test_two_endpoints_for_one_address_warn(stripe):
    stripe["endpoints"].append(dict(stripe["endpoints"][0]))

    assert any(level == "warn" and "2 endpoints" in text for level, text in _levels())


@pytest.mark.parametrize(("key", "mode"), [
    ("sk_live_abc", "live"), ("rk_test_abc", "test"), ("pk_live_abc", "unknown"), ("", "unknown"),
])
def test_the_mode_is_read_off_the_prefix(settings, key, mode):
    settings.STRIPE_SECRET_KEY = key

    assert stripe_api.mode() == mode
