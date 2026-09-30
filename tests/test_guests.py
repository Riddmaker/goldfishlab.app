"""Phase 9 G: trying the site without an account, then saving the deck.

What these tests hold:

1. **A file is enough.** An anonymous upload makes a guest, signs it in for
   this browser, imports the deck and starts the simulation by itself. A file
   that cannot be read makes nothing - not even a guest.
2. **A guest is fenced.** One deck at a time, the guest plan's limits, and no
   billing or account screens.
3. **Saving is claiming.** The save form makes a real account through
   allauth, moves every row the guest made to it and deletes the guest; the
   confirmation link then signs in and lands on the saved deck.
4. **An unsaved guest ends.** Deleted with everything it made after a day.
"""

import re
from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from billing import quotas
from decks.models import Deck
from guests import services
from simulations import services as simulations
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-long-test-passphrase-9"
DECK_TEXT = "// Commander\n1 Chainer, Dementia Master\n// Deck\n1 Sol Ring\n36 Swamp\n"


class FakeRedis:
    """The concurrency counter, in memory (as in test_simulations_runs)."""

    def __init__(self):
        self.values = {}

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def decr(self, key):
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    def expire(self, key, seconds):
        return True

    def set(self, key, value, ex=None):
        self.values[key] = value
        return True


@pytest.fixture(autouse=True)
def _no_workers(monkeypatch):
    """No Redis, no Celery: record what would have been queued."""
    client = FakeRedis()
    monkeypatch.setattr(simulations, "_redis", lambda: client)
    queued = []
    monkeypatch.setattr(simulations, "_dispatch", lambda run, tasks: queued.append(run))
    return queued


@pytest.fixture(autouse=True)
def _catalogue(catalogue):
    """Every guest test imports a deck, so every one needs the cards."""


def upload(client, text=DECK_TEXT):
    return client.post(reverse("guests:try"), {"source": "paste", "text": text})


def the_guest():
    return User.objects.get(is_guest=True)


@pytest.fixture
def guest(client):
    upload(client)
    return the_guest()


# --- a file is enough ------------------------------------------------------------

def test_an_upload_makes_a_guest_and_starts_its_simulation(
        client, _no_workers, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = upload(client)

    guest = the_guest()
    run = SimulationRun.objects.get(owner=guest)
    assert response.status_code == 302
    assert response.url == run.get_absolute_url()
    assert run.owner == guest and run.deck.owner == guest
    assert run.games_total == services.TRIAL_GAMES
    assert _no_workers == [run]
    assert client.get(run.get_absolute_url()).status_code == 200, "signed in as the guest"


def test_the_guest_row_is_unusable_outside_its_session(client):
    upload(client)
    guest = the_guest()

    assert guest.email.endswith("@" + services.GUEST_DOMAIN)
    assert not guest.has_usable_password()
    assert quotas.plan_for(guest).slug == services.GUEST_PLAN


COMMANDER_CSV = (b'Quantity,Name,Tags\n1,"Chainer, Dementia Master",Commander\n'
                 b"1,Sol Ring,\n36,Swamp,\n")
PLAIN_CSV = b"Quantity,Name\n1,Sol Ring\n36,Swamp\n"


def test_a_deck_is_named_after_its_commander_not_its_file(client):
    export = SimpleUploadedFile("archidekt-collection-export-2026-09-15.csv", COMMANDER_CSV)

    client.post(reverse("guests:try"), {"source": "file", "file": export})

    assert the_guest().decks.get().name == "Chainer, Dementia Master"


def test_without_a_commander_the_file_names_it_without_its_extension(client):
    export = SimpleUploadedFile("my-list.csv", PLAIN_CSV)

    client.post(reverse("guests:try"), {"source": "file", "file": export})

    assert the_guest().decks.get().name == "my-list"


def test_an_unreadable_upload_makes_nothing(client):
    response = client.post(reverse("guests:try"), {"source": "paste", "text": ""})

    assert response.status_code == 200
    assert not User.objects.filter(is_guest=True).exists()


def test_a_second_upload_replaces_the_first_deck(client, guest):
    first = guest.decks.get()

    upload(client, DECK_TEXT.replace("36 Swamp", "35 Swamp\n1 Arcane Signet"))

    assert User.objects.filter(is_guest=True).count() == 1, "the same guest"
    assert not Deck.objects.filter(pk=first.pk).exists()
    assert guest.decks.count() == 1


def test_a_signed_in_account_is_sent_to_its_own_importer(client):
    user = User.objects.create_user(email="real@example.com", password=PASSWORD)
    client.force_login(user)

    response = client.get(reverse("guests:try"))

    assert response.status_code == 302
    assert response.url == reverse("decks:import")


def test_new_guests_are_rate_limited_per_address(client, pinned_window):
    for _ in range(5):
        client.logout()
        assert upload(client).status_code == 302
    client.logout()

    assert upload(client).status_code == 429
    assert User.objects.filter(is_guest=True).count() == 5


def test_the_home_page_offers_the_trial(client):
    body = client.get(reverse("home")).content.decode()

    assert reverse("guests:try") in body
    assert "no account" in body


# --- a guest is fenced --------------------------------------------------------------

@pytest.mark.parametrize("path, target", [
    ("/billing/plans/", "guests:save"),
    ("/account/data/", "guests:save"),
    ("/accounts/email/", "guests:save"),
    ("/accounts/password/change/", "guests:save"),
    ("/accounts/signup/", "guests:save"),
    ("/decks/import/", "guests:try"),
])
def test_a_guest_never_sees_account_screens(client, guest, path, target):
    response = client.get(path)

    assert response.status_code == 302
    assert response.url == reverse(target)


def test_the_sign_in_page_ends_the_guest_session(client, guest):
    response = client.get(reverse("account_login"))

    assert response.status_code == 200, "the form, not a redirect for a signed-in user"
    assert "_auth_user_id" not in client.session


def test_the_header_offers_saving_and_no_email(client, guest):
    body = client.get(reverse("decks:list")).content.decode()

    assert reverse("guests:save") in body
    assert guest.email not in body
    assert reverse("accounts:data") not in body


def test_the_run_form_offers_only_what_the_guest_plan_runs(client, guest):
    deck = guest.decks.get()
    field = client.get(deck.get_absolute_url()).context["run_form"]["games"].field

    assert max(value for value, _ in field.choices) == services.TRIAL_GAMES
    assert field.initial == services.TRIAL_GAMES


def test_guests_together_cannot_fill_the_workers(client, guest, monkeypatch):
    monkeypatch.setattr(services, "MAX_ACTIVE_GUEST_RUNS", 1)
    assert SimulationRun.objects.filter(owner=guest).count() == 1

    with pytest.raises(simulations.SimulationRefused, match="trying Goldfish Lab"):
        simulations.start_run(owner=guest, deck=guest.decks.get(), games=1000, turns=3)


def test_accounts_do_not_wait_for_guests(client, guest, monkeypatch):
    monkeypatch.setattr(services, "MAX_ACTIVE_GUEST_RUNS", 1)
    user = User.objects.create_user(email="real@example.com", password=PASSWORD)
    deck = guest.decks.get()
    deck.owner = user
    deck.save()

    assert simulations.start_run(owner=user, deck=deck, games=1000, turns=3)


# --- saving is claiming ---------------------------------------------------------------

def save(client, email="new@example.com", deck_name="My Chainer"):
    return client.post(reverse("guests:save"), {
        "deck_name": deck_name, "email": email, "password1": PASSWORD,
    })


def test_the_save_form_names_the_deck_after_the_file(client, guest):
    form = client.get(reverse("guests:save")).context["form"]

    assert form.initial["deck_name"] == guest.decks.get().name
    assert list(form.fields)[:3] == ["deck_name", "email", "password1"]


def test_saving_moves_everything_to_the_new_account(client, guest):
    deck = guest.decks.get()
    run = SimulationRun.objects.get(owner=guest)

    response = save(client)

    user = User.objects.get(email="new@example.com")
    deck.refresh_from_db()
    run.refresh_from_db()
    assert response.status_code == 302
    assert response.url == reverse("account_email_verification_sent")
    assert not user.is_guest
    assert (deck.owner, deck.name) == (user, "My Chainer")
    assert run.owner == user
    assert not User.objects.filter(pk=guest.pk).exists(), "the guest is gone"
    assert quotas.plan_for(user).slug == "free"


def test_the_confirmation_link_signs_in_on_the_saved_deck(client, guest, mailoutbox):
    deck = guest.decks.get()
    save(client)

    link = re.search(r"/accounts/confirm-email/[^/\s]+/", mailoutbox[-1].body).group(0)
    response = client.post(link)

    user = User.objects.get(email="new@example.com")
    assert response.status_code == 302
    assert response.url == deck.get_absolute_url()
    assert client.session["_auth_user_id"] == str(user.pk)
    assert EmailAddress.objects.get(user=user).verified


def test_an_address_with_an_account_keeps_it_and_says_nothing(client, guest, mailoutbox):
    existing = User.objects.create_user(email="taken@example.com", password=PASSWORD)

    response = save(client, email="taken@example.com")

    assert response.status_code == 302
    assert response.url == reverse("account_email_verification_sent"), "no enumeration"
    assert User.objects.filter(email="taken@example.com").get() == existing
    assert guest.decks.count() == 1 and not existing.decks.exists(), "nothing moved"


def test_a_weak_password_keeps_the_guest(client, guest):
    response = client.post(reverse("guests:save"), {
        "deck_name": "x", "email": "new@example.com", "password1": "123",
    })

    assert response.status_code == 200
    assert "_auth_user_id" in client.session
    assert User.objects.filter(pk=guest.pk).exists()


@pytest.mark.parametrize("who", ["anonymous", "account"])
def test_only_a_guest_can_save(client, who):
    if who == "account":
        client.force_login(User.objects.create_user(email="real@example.com", password=PASSWORD))

    response = client.get(reverse("guests:save"))

    assert response.status_code == 302
    assert response.url == reverse("decks:list" if who == "account" else "guests:try")


def test_a_real_account_cannot_be_claimed():
    user = User.objects.create_user(email="real@example.com", password=PASSWORD)
    other = User.objects.create_user(email="other@example.com", password=PASSWORD)

    with pytest.raises(ValueError):
        services.claim(user, other)


def test_claiming_moves_every_owned_model():
    """Driven by the privacy export's list, which test_privacy keeps complete."""
    moved = {model._meta.label for model, _ in services._owned()}

    assert {"decks.Deck", "decks.DeckImport", "decks.PendingImport",
            "simulations.SimulationRun", "simulations.CardAnnotation",
            "playtest.PlaytestSession"} <= moved
    assert not any(label.startswith("billing.") for label in moved)


# --- an unsaved guest ends ---------------------------------------------------------------

def test_guests_expire_after_a_day_with_everything_they_made(client, guest):
    User.objects.filter(pk=guest.pk).update(date_joined=timezone.now() - timedelta(hours=25))
    young = User.objects.create_user(email="young@guest.invalid", is_guest=True)
    user = User.objects.create_user(email="old@example.com", password=PASSWORD)
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=90))

    assert services.expire() == 1

    assert not User.objects.filter(pk=guest.pk).exists()
    assert not Deck.objects.filter(owner_id=guest.pk).exists()
    assert not SimulationRun.objects.filter(owner_id=guest.pk).exists()
    assert User.objects.filter(pk__in=[young.pk, user.pk]).count() == 2


def test_the_expiry_runs_every_hour(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["guests-expire"]

    assert entry["task"] == "guests.expire"
    assert entry["schedule"].hour == set(range(24))


def test_the_privacy_policy_names_the_guest_and_its_day(client):
    body = client.get(reverse("privacy")).content.decode()

    assert "without an account" in body
    assert "24 hours" in body
