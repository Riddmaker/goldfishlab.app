"""Account, plan and the law, as Lauf 1 found them (P20 F3).

E1.7  accounts with an empty language - only the switcher set it;
E1.20 the deck limit on an import put a log line on the form;
E1.58 a cancellation in Stripe's portal never reached the app;
E1.14 "Save your deck" made an account without naming terms or privacy;
E1.27 a deck summary cost a run without a word;
E1.21 the import limit and the trial's limits were on no page.
"""

import types

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import translation

from accounts import language
from accounts.adapter import AccountAdapter
from billing import quotas, services
from billing.models import Plan, Subscription, UsageRecord
from decks import services as deck_services
from simulations import services as simulations
from simulations import summary
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
PRICE = "price_koi_test"


@pytest.fixture
def user():
    return User.objects.create_user(email="f3@example.com", password="pw-for-test-only")


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def french(settings):
    settings.LANGUAGES = [("en", "English"), ("fr", "Français")]


# --- E1.7: the account's language -----------------------------------------


def test_a_new_account_keeps_the_language_it_was_made_in(french):
    form = types.SimpleNamespace(cleaned_data={"email": "neu@example.com",
                                               "password1": "a-long-passphrase-9"})
    with translation.override("fr"):
        made = AccountAdapter().save_user(None, User(), form)

    made.refresh_from_db()
    assert made.language == "fr"


def test_a_language_that_is_switched_off_is_not_kept(user):
    with translation.override("fr"):
        assert not language.remember(user)
    assert user.language == ""


def test_signing_in_fills_an_empty_language_and_keeps_a_chosen_one(client, user, french):
    with translation.override("fr"):
        client.force_login(user)
    user.refresh_from_db()
    assert user.language == "fr"

    user.language = "en"
    user.save()
    with translation.override("fr"):
        client.force_login(user)
    user.refresh_from_db()
    assert user.language == "en", "a language somebody has is never overwritten"


def test_a_guests_chosen_language_beats_the_one_the_account_was_made_in(user):
    from guests import services as guests

    guest = User.objects.create_user(email="g@guest.invalid", password=None, is_guest=True,
                                     language="pt-br")
    user.language = "en"
    user.save()

    guests.claim(guest, user)

    user.refresh_from_db()
    assert user.language == "pt-br"


# --- E1.20: the deck limit on an import -----------------------------------


def test_the_deck_limit_on_an_import_is_a_page_with_a_way_on(signed_in, user, catalogue):
    for number in range(3):
        deck_services.import_deck(owner=user, raw=b"1 Sol Ring\n", name=f"Deck {number}")

    response = signed_in.post(reverse("decks:import"),
                              {"source": "paste", "text": "1 Sol Ring\n"})

    assert response.status_code == 402
    body = response.content.decode()
    assert "You have 3 of 3 decks." in body
    assert "Deleting a deck frees its place." in body
    assert "first of the month" not in body
    assert "decks_owned" not in body, "never the exception's log line"


# --- E1.58: a cancellation set by date ------------------------------------


def _event(kind, user, **fields):
    obj = {"id": "sub_f3", "customer": "cus_f3", "status": "active",
           "cancel_at_period_end": False,
           "metadata": {"user_id": str(user.pk), "plan_slug": "koi"},
           "items": {"data": [{"price": {"id": PRICE}, "current_period_end": 1800000000}]}}
    obj.update(fields)
    return {"id": f"evt_{kind}_{len(fields)}_{fields.get('cancel_at')}", "type": kind,
            "data": {"object": obj}}


@pytest.fixture
def paying(user):
    plan = Plan.objects.get(slug="koi")
    plan.stripe_price_id = PRICE
    plan.save(update_fields=["stripe_price_id"])
    services.subscription_for(user)
    services.apply_event(_event("customer.subscription.created", user))
    return user


def test_a_cancellation_by_date_ends_soon_and_shows_that_date(paying, signed_in):
    services.apply_event(_event("customer.subscription.updated", paying, cancel_at=1810000000))

    subscription = Subscription.objects.get(user=paying)
    assert subscription.ends_soon
    assert subscription.ends_on.timestamp() == 1810000000, "Stripe's date, not the period's"
    body = signed_in.get(reverse("billing:plans")).content.decode()
    assert "This plan ends on" in body


def test_taking_the_cancellation_back_clears_it(paying):
    services.apply_event(_event("customer.subscription.updated", paying, cancel_at=1810000000))
    services.apply_event(_event("customer.subscription.updated", paying, cancel_at=None))

    subscription = Subscription.objects.get(user=paying)
    assert subscription.cancel_at is None
    assert not subscription.ends_soon


def test_the_end_of_the_subscription_clears_it(paying):
    services.apply_event(_event("customer.subscription.updated", paying, cancel_at=1810000000))
    services.apply_event(_event("customer.subscription.deleted", paying))

    assert Subscription.objects.get(user=paying).cancel_at is None


# --- E1.14: consent on "Save your deck" -----------------------------------


def test_saving_a_guest_deck_names_the_terms_and_the_privacy_policy(client, catalogue):
    client.post(reverse("guests:try"), {"source": "paste",
                                        "text": "// Commander\n1 Chainer, Dementia Master\n"})

    body = client.get(reverse("guests:save")).content.decode()
    assert "By creating an account you accept the" in body
    assert f'href="{reverse("terms")}"' in body
    assert f'href="{reverse("privacy")}"' in body


# --- E1.27: the summary's cost --------------------------------------------


def test_a_summary_with_no_room_is_skipped_and_the_run_says_so(user, signed_in, catalogue,
                                                               monkeypatch):
    monkeypatch.setattr(summary, "due", lambda owner, deck, print_=None: True)
    monkeypatch.setattr(simulations, "_take_slot", lambda owner, limit: True)
    monkeypatch.setattr(simulations, "_dispatch", lambda run, tasks: None)
    deck = deck_services.import_deck(owner=user, raw=b"1 Sol Ring\n", name="Room").deck
    limit = quotas.plan_for(user).max_runs_per_month
    UsageRecord.objects.create(user=user, period_start=quotas.period_start(),
                               metric=UsageRecord.Metric.RUNS_STARTED, amount=limit - 1)

    run = simulations.start_run(owner=user, deck=deck, games=1000, turns=3)

    assert run.summary_skipped
    body = signed_in.get(run.get_absolute_url()).content.decode()
    assert "No new deck summary this time" in body


def test_the_deck_page_says_a_summary_counts_as_a_simulation(user, signed_in, catalogue,
                                                             monkeypatch):
    monkeypatch.setattr(summary, "due", lambda owner, deck, print_=None: True)
    deck = deck_services.import_deck(owner=user, raw=b"1 Sol Ring\n", name="Due").deck

    body = signed_in.get(deck.get_absolute_url()).content.decode()
    assert "which counts as one more simulation" in body


def test_an_ordinary_run_skips_nothing(user, catalogue, monkeypatch):
    monkeypatch.setattr(simulations, "_take_slot", lambda owner, limit: True)
    monkeypatch.setattr(simulations, "_dispatch", lambda run, tasks: None)
    deck = deck_services.import_deck(owner=user, raw=b"1 Sol Ring\n", name="Plain").deck

    run = simulations.start_run(owner=user, deck=deck, games=1000, turns=3)

    assert not run.summary_skipped
    assert not SimulationRun.objects.filter(summary_skipped=True).exists()


# --- E1.21: every limit on a page -----------------------------------------


def test_the_pricing_page_names_the_trial_and_the_import_limit(client):
    from guests import services as guests

    body = client.get(reverse("pricing")).content.decode()
    assert "Without an account:" in body
    assert (f"simulated {guests.TRIAL_GAMES:,} times over {guests.TRIAL_TURNS} turns"
            in body), "the numbers come from the trial's own constants"
    assert "Deck lists imported a month:" in body


def test_the_account_page_counts_the_imports(signed_in):
    body = signed_in.get(reverse("billing:plans")).content.decode()
    assert "Deck lists imported" in body


def test_english_is_never_stored_because_it_would_pin_the_address(client, user, french):
    """An empty language already means English; a stored one beats /fr/."""
    with translation.override("en"):
        client.force_login(user)

    user.refresh_from_db()
    assert user.language == ""
