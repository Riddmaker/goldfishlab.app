"""P19a: the cards the engine could not read reach the operator, not the player's name."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from cards.models import DerivedProfile, OracleCard
from decks import resolve, services
from decks.importers import PlainTextParser
from decks.models import Deck
from simulation import ENGINE_VERSION
from simulations import tasks, unread
from simulations.engine import adapter
from simulations.models import CardAnnotation, UnreadCard

User = get_user_model()

LIST = b"1 Smothering Tithe\n1 Cabal Ritual\n1 Sol Ring\n1 Swamp\n"


@pytest.fixture
def user(db):
    return User.objects.create_user(email="unread@example.com", password="pw-for-test-only")


@pytest.fixture
def run_now(monkeypatch):
    """The worker, in the test: the task runs where it is queued."""
    monkeypatch.setattr(tasks.record_unread, "delay", lambda deck_id: tasks.record_unread(deck_id))


def upload(user, django_capture_on_commit_callbacks, raw=LIST, deck=None):
    with django_capture_on_commit_callbacks(execute=True):
        return services.import_deck(owner=user, raw=raw, name="Test", deck=deck).deck


def card(name):
    return OracleCard.objects.get(name=name)


def test_an_upload_counts_the_cards_the_engine_could_not_read(
        catalogue, user, run_now, django_capture_on_commit_callbacks):
    upload(user, django_capture_on_commit_callbacks)

    names = set(UnreadCard.objects.values_list("oracle_card__name", flat=True))
    assert {"Smothering Tithe", "Cabal Ritual"} <= names
    assert "Sol Ring" not in names and "Swamp" not in names
    orchard = UnreadCard.objects.get(oracle_card__name="Smothering Tithe")
    assert orchard.seen == 1
    assert orchard.status == UnreadCard.Status.OPEN
    assert orchard.engine_version == ENGINE_VERSION
    assert any("how much" in reason for reason in orchard.reasons)


def test_nothing_on_the_row_points_at_a_person_or_a_deck():
    related = {field.related_model for field in UnreadCard._meta.get_fields()
               if field.is_relation}
    assert related == {OracleCard}


def test_a_second_upload_counts_again_and_outlives_the_deck(
        catalogue, user, run_now, django_capture_on_commit_callbacks):
    deck = upload(user, django_capture_on_commit_callbacks)
    upload(user, django_capture_on_commit_callbacks, deck=deck)
    deck.delete()

    assert UnreadCard.objects.get(oracle_card__name="Smothering Tithe").seen == 2


def test_a_deck_deleted_before_the_worker_came_is_skipped(db):
    assert tasks.record_unread("00000000-0000-0000-0000-000000000000") == 0


def test_the_sites_own_precons_are_not_counted(catalogue, user):
    deck = Deck.objects.create(owner=user, name="Precon")
    services.fill(deck, resolve.resolve(list(PlainTextParser().parse("1 Command Tower\n"))))
    assert not UnreadCard.objects.exists()


def answerable(name: str) -> OracleCard:
    """A card whose only gap is one an answer can close.

    A land the reader says makes mana but that has no ability the engine can
    see: Maze of Ith was one until engine version 8 read it as making none
    (P19 R4). The profile is made that way here, since no card in the sample
    is any longer.
    """
    DerivedProfile.objects.filter(oracle_card__name=name).update(
        produces_mana=True, needs_review=False, review_reasons=[])
    return card(name)


def test_a_players_own_answer_does_not_hide_what_the_engine_cannot_read(catalogue, user):
    """Their answer makes the card work on their deck; the engine still could not read it."""
    maze = answerable("Maze of Ith")
    deck = Deck.objects.create(owner=user, name="Mine")
    deck.entries.create(oracle_card=maze, quantity=1)
    CardAnnotation.objects.create(owner=user, oracle_card=maze,
                                  overrides={"mana_produces": {"C": 1}})

    readable_for_them = [r for r in adapter.readings(deck) if r.oracle_card.name == "Maze of Ith"]
    assert not readable_for_them[0].unreadable

    unread.record(deck)
    assert UnreadCard.objects.filter(oracle_card__name="Maze of Ith").exists()


def test_a_built_in_answer_counts_as_the_engine_reading_it(catalogue, user):
    maze = answerable("Maze of Ith")
    deck = Deck.objects.create(owner=user, name="Mine")
    deck.entries.create(oracle_card=maze, quantity=1)
    CardAnnotation.objects.create(owner=None, oracle_card=maze,
                                  overrides={"mana_produces": {"C": 1}})

    unread.record(deck)
    assert not UnreadCard.objects.filter(oracle_card__name="Maze of Ith").exists()


def make_row(name, **fields):
    defaults = {"reasons": ["old reason"], "seen": 3, "last_seen": timezone.now(),
                "engine_version": ENGINE_VERSION}
    return UnreadCard.objects.create(oracle_card=card(name), **{**defaults, **fields})


def test_recheck_closes_what_the_engine_reads_now_and_keeps_the_rest(catalogue):
    sol = make_row("Sol Ring")
    tower = make_row("Command Tower")
    orchard = make_row("Smothering Tithe")

    assert unread.recheck() == 2

    for row in (sol, tower, orchard):
        row.refresh_from_db()
    assert sol.status == UnreadCard.Status.READ
    assert sol.read_since == ENGINE_VERSION
    # Engine version 5 reads a choice of colours (P19 R1).
    assert tower.status == UnreadCard.Status.READ
    assert orchard.status == UnreadCard.Status.OPEN
    assert orchard.reasons != ["old reason"]


def test_a_card_lost_again_opens_and_is_mailed_again(catalogue, user):
    tower_row = make_row("Cabal Ritual", status=UnreadCard.Status.READ, read_since=3,
                    mailed_at=timezone.now())
    deck = Deck.objects.create(owner=user, name="Mine")
    deck.entries.create(oracle_card=tower_row.oracle_card, quantity=1)

    unread.record(deck)

    tower_row.refresh_from_db()
    assert tower_row.status == UnreadCard.Status.OPEN
    assert tower_row.read_since is None and tower_row.mailed_at is None
    assert tower_row.seen == 4


def test_one_set_aside_stays_set_aside(catalogue, user):
    orchard = make_row("Smothering Tithe", status=UnreadCard.Status.WONT_FIX)
    deck = Deck.objects.create(owner=user, name="Mine")
    deck.entries.create(oracle_card=orchard.oracle_card, quantity=1)

    unread.record(deck)

    orchard.refresh_from_db()
    assert orchard.status == UnreadCard.Status.WONT_FIX
    assert orchard.seen == 4


def test_the_weekly_mail_names_only_new_open_cards_once(catalogue, settings):
    settings.ALERT_EMAIL = "ops@example.com"
    settings.SITE_URL = "https://goldfishlab.app"
    make_row("Command Tower", seen=9)
    make_row("Cabal Ritual", mailed_at=timezone.now() - timedelta(days=7))
    make_row("Smothering Tithe", status=UnreadCard.Status.WONT_FIX)

    assert unread.weekly_mail() == 1
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["ops@example.com"]
    assert "Command Tower (seen 9x)" in message.body
    assert "Cabal Ritual" not in message.body and "Smothering Tithe" not in message.body
    path = reverse("admin:simulations_unreadcard_change", args=[card("Command Tower").pk])
    assert f"https://goldfishlab.app{path}" in message.body

    assert unread.weekly_mail() == 0
    assert len(mail.outbox) == 1


def test_without_an_address_the_cards_wait_for_one(catalogue, settings):
    settings.ALERT_EMAIL = ""
    make_row("Command Tower")

    assert unread.weekly_mail() == 0
    assert not mail.outbox
    assert UnreadCard.objects.get().mailed_at is None


def test_the_admin_shows_the_card_and_what_players_answered(catalogue, admin_client, user):
    row = make_row("Cabal Ritual")
    CardAnnotation.objects.create(owner=user, oracle_card=row.oracle_card,
                                  overrides={"mana_produces": {"C": 1}})

    listing = admin_client.get(reverse("admin:simulations_unreadcard_changelist"))
    page = admin_client.get(reverse("admin:simulations_unreadcard_change", args=[row.pk]))

    assert listing.status_code == 200 and "Cabal Ritual" in listing.content.decode()
    body = page.content.decode()
    assert "1 player(s)" in body
    assert "mana_produces" in body
    assert user.email not in body


def test_the_privacy_policy_names_it(client):
    body = client.get(reverse("privacy")).content.decode()
    assert "Cards the engine could not read in a deck you uploaded" in body
