"""P5: Goldfish, Koi and Kraken, by the month or by the year."""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.urls import reverse

from billing import services, stripe_api
from billing.models import Plan

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def signed_in(client):
    user = User.objects.create_user(email="plans@example.com", password="pw-test-only")
    client.force_login(user)
    return client


@pytest.fixture
def sold(monkeypatch):
    """Koi with a month's and a year's Stripe price; the call recorded."""
    Plan.objects.filter(slug="koi").update(stripe_price_id="price_koi_month",
                                           stripe_annual_price_id="price_koi_year")
    monkeypatch.setattr(stripe_api, "is_configured", lambda: True)
    sent = {}

    def _checkout(**params):
        sent.update(params)
        return SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_test")

    monkeypatch.setattr(stripe_api, "checkout_session", _checkout)
    return sent


@pytest.mark.parametrize(("slug", "name", "month", "year", "turns"), [
    ("free", "Goldfish", 0, 0, 9),
    ("koi", "Koi", 400, 3600, 12),
    ("kraken", "Kraken", 900, 8400, 15),
])
def test_the_three_plans(slug, name, month, year, turns):
    plan = Plan.objects.get(slug=slug)
    assert (plan.name, plan.price_chf_cents, plan.max_turns) == (name, month, turns)
    assert all(plan.annual_cents(c) == year for c in ("chf", "eur", "usd"))
    assert all(plan.price_cents(c) == month for c in ("chf", "eur", "usd"))
    assert plan.features == {}, "a plan must not seem to sell what was never built"


def test_no_plan_carries_a_wizards_trademark():
    names = {plan.name.casefold() for plan in Plan.objects.all()}
    assert not names & {"planeswalker", "archmage"}
    assert not Plan.objects.filter(slug__in=["planeswalker", "archmage"]).exists()


def test_guests_keep_six_turns():
    assert Plan.objects.get(slug="guest").max_turns == 6


def test_the_old_lab_price_cannot_be_bought():
    """Archmage's Stripe price billed CHF 12; the rename must not keep it."""
    Plan.objects.filter(slug="kraken").update(slug="archmage", name="Archmage",
                                              stripe_price_id="price_old_12")
    migration = import_module("billing.migrations.0009_plans_goldfish_koi_kraken")
    migration.rename(django_apps, None)
    kraken = Plan.objects.get(slug="kraken")
    assert kraken.stripe_price_id == ""
    assert kraken.price_chf_cents == 900


def test_a_subscription_keeps_its_plan_through_the_rename():
    user = User.objects.create_user(email="sub@example.com", password="pw-test-only")
    koi = Plan.objects.get(slug="koi")
    Plan.objects.filter(pk=koi.pk).update(slug="planeswalker", name="Planeswalker")
    user.subscription.plan = koi
    user.subscription.save()
    import_module("billing.migrations.0009_plans_goldfish_koi_kraken").rename(django_apps, None)
    user.subscription.refresh_from_db()
    assert user.subscription.plan.slug == "koi"


def test_the_year_is_checked_out_on_the_annual_price(signed_in, sold):
    signed_in.post(reverse("billing:checkout", args=["koi"]), {"interval": "year"})
    assert sold["line_items"] == [{"price": "price_koi_year", "quantity": 1}]
    message = sold["custom_text"]["terms_of_service_acceptance"]["message"]
    assert "renews every year" in message


def test_the_month_is_the_default(signed_in, sold):
    signed_in.post(reverse("billing:checkout", args=["koi"]), {"interval": "anything"})
    assert sold["line_items"] == [{"price": "price_koi_month", "quantity": 1}]
    assert "renews every month" in sold["custom_text"]["terms_of_service_acceptance"]["message"]


def test_no_year_without_an_annual_price(signed_in, sold):
    Plan.objects.filter(slug="koi").update(stripe_annual_price_id="")
    response = signed_in.post(reverse("billing:checkout", args=["koi"]), {"interval": "year"})
    assert response["Location"] == reverse("billing:plans")
    assert not sold


def test_the_webhook_knows_the_annual_price():
    koi = Plan.objects.get(slug="koi")
    koi.stripe_annual_price_id = "price_koi_year"
    koi.save()
    obj = {"items": {"data": [{"price": {"id": "price_koi_year"}}]}}
    assert services._plan_from(obj) == koi


def test_the_plans_page_offers_both(signed_in, sold):
    body = signed_in.get(reverse("billing:plans")).content.decode()
    assert "or CHF 36 a year" in body
    assert "Pay yearly: CHF 36" in body
    assert 'name="interval" value="year"' in body
    assert "or CHF 84 a year" in body  # Kraken: shown, not yet buyable


def test_the_free_plan_reads_goldfish_in_german(signed_in, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    signed_in.cookies["django_language"] = "de"
    body = signed_in.get(reverse("billing:plans"), follow=True).content.decode()
    assert "Goldfisch" in body


def test_the_terms_name_the_yearly_renewal(client):
    body = client.get(reverse("terms")).content.decode()
    assert "monthly or yearly as you choose at checkout" in body
    assert "A yearly plan's renewal is" in body
