"""Phase 11 G (K13, P9, P10): round prices per currency, switched off by default.

CHF for Switzerland and Liechtenstein, EUR for the EU/EEA, USD everywhere else,
the same round number in each - and Checkout fixed to the currency the page
showed. Off (the default, until P10 is settled), nothing changes: Swiss francs
on the page and no `currency` sent to Stripe.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from billing import currency, services, stripe_api
from billing.models import Plan

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def signed_in(client):
    user = User.objects.create_user(email="prices@example.com", password="pw-test-only")
    client.force_login(user)
    return client


@pytest.fixture
def sold(monkeypatch):
    """Planeswalker with a Stripe price, and the Stripe call recorded instead of made."""
    plan = Plan.objects.get(slug="planeswalker")
    plan.stripe_price_id = "price_planeswalker_test"
    plan.save(update_fields=["stripe_price_id"])
    monkeypatch.setattr(stripe_api, "is_configured", lambda: True)
    sent = {}

    def _checkout(**params):
        sent.update(params)
        return SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_test")

    monkeypatch.setattr(stripe_api, "checkout_session", _checkout)
    return sent


@pytest.mark.parametrize(("country", "expected"), [
    ("CH", "chf"), ("LI", "chf"), ("li", "chf"),
    ("DE", "eur"), ("FR", "eur"), ("PL", "eur"), ("SE", "eur"), ("NO", "eur"), ("IS", "eur"),
    ("US", "usd"), ("GB", "usd"), ("JP", "usd"),
    ("XX", "usd"), ("T1", "usd"), ("", "usd"),
])
def test_the_country_picks_the_currency(country, expected):
    assert currency.for_country(country) == expected


def test_the_eu_and_eea_list_is_complete():
    """27 EU members plus Iceland and Norway; Liechtenstein pays in francs."""
    assert len(currency.EURO_COUNTRIES) == 29
    assert not currency.EURO_COUNTRIES & currency.FRANC_COUNTRIES


@pytest.mark.parametrize(("cents", "code", "text"), [
    (400, "chf", "CHF 4"), (400, "eur", "€4"), (1200, "usd", "$12"), (450, "usd", "$4.50"),
])
def test_a_price_reads_as_a_round_number(cents, code, text):
    assert currency.label(cents, code) == text


def test_every_paid_plan_has_the_same_number_in_each_currency():
    for plan in Plan.objects.exclude(price_chf_cents=0):
        assert plan.prices == {code: plan.price_chf_cents for code in currency.CURRENCIES}
    assert Plan.objects.get(slug="planeswalker").price_cents("eur") == 400
    assert Plan.objects.get(slug="archmage").price_cents("usd") == 1200
    assert Plan.objects.get(slug="free").prices == {}


def test_switched_off_everyone_sees_francs_and_checkout_sends_no_currency(
        signed_in, sold, settings):
    settings.LOCAL_PRICES = False

    body = signed_in.get(reverse("billing:plans"), headers={"CF-IPCountry": "DE"}).content.decode()
    signed_in.post(reverse("billing:checkout", args=["planeswalker"]),
                   headers={"CF-IPCountry": "DE"})

    assert "CHF 4.00 / month" in body
    assert "€" not in body
    assert "currency" not in sent_keys(sold)


@pytest.mark.parametrize(("country", "shown", "code"), [
    ("CH", "CHF 4 / month", "chf"), ("DE", "€4 / month", "eur"),
    ("US", "$4 / month", "usd"), (None, "$4 / month", "usd"),
])
def test_switched_on_the_page_and_checkout_agree(signed_in, sold, settings,
                                                 country, shown, code):
    settings.LOCAL_PRICES = True
    headers = {"CF-IPCountry": country} if country else {}

    body = signed_in.get(reverse("billing:plans"), headers=headers).content.decode()
    signed_in.post(reverse("billing:checkout", args=["planeswalker"]), headers=headers)

    assert shown in body
    assert sold["currency"] == code


def test_switched_on_checkout_no_longer_promises_a_converted_price(signed_in, sold, settings):
    settings.LOCAL_PRICES = True
    settings.STRIPE_MANAGED_PAYMENTS = True

    plans = signed_in.get(reverse("billing:plans")).content.decode()
    terms = signed_in.get(reverse("terms")).content.decode()

    assert "own currency" not in plans
    assert "own currency" not in terms
    assert "euros or US" in terms


def test_a_fixed_currency_is_not_a_parameter_managed_payments_forbids():
    assert "currency" not in services.MANAGED_PAYMENTS_FORBIDDEN


def sent_keys(sent: dict) -> set:
    assert sent, "the checkout was never started"
    return set(sent)
