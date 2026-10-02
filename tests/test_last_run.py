"""Phase 11 F11: the deck page shows the last run, and says when it is stale.

Runs were always kept; the deck page only showed them folded under "Earlier
runs and games". What these tests hold:

1. **A run remembers the deck it played.** `deck_print` is the summary's
   fingerprint at the start.
2. **The deck page opens on the last report.** The newest finished run, with
   a link to it, and "Simulate again" on the button.
3. **A changed deck says so** - and a run from before `deck_print` says
   nothing rather than guess.
4. **A run still going shows its progress** above the last finished one.
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from decks import services as deck_services
from simulations import services, summary
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"
STALE = "The deck has changed since this run."


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="lastrun@example.com", password="pw-test-1234")


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Last run deck",
        filename="sample.csv",
    ).deck


def _run(deck, status=SimulationRun.Status.DONE, deck_print=None, games=2000):
    return SimulationRun.objects.create(
        owner=deck.owner, deck=deck, games_total=games, seed=1, status=status,
        deck_print=summary.fingerprint(deck) if deck_print is None else deck_print,
    )


def _page(client, deck):
    client.force_login(deck.owner)
    response = client.get(deck.get_absolute_url())
    assert response.status_code == 200
    return response.content.decode()


def test_a_run_remembers_the_deck_it_played(deck, monkeypatch):
    monkeypatch.setattr(services, "_take_slot", lambda owner, limit: True)
    monkeypatch.setattr(services, "_dispatch", lambda run, tasks_module: None)

    run = services.start_run(owner=deck.owner, deck=deck, games=1000, turns=3)

    assert run.deck_print == summary.fingerprint(deck)


def test_without_a_run_there_is_no_box_and_the_button_says_simulate(client, deck):
    body = _page(client, deck)

    assert "Your last run" not in body
    assert "Simulate again" not in body


def test_the_deck_page_opens_on_the_last_finished_run(client, deck):
    older = _run(deck, games=1000)
    newest = _run(deck, games=2000)
    _run(deck, status=SimulationRun.Status.FAILED)

    body = _page(client, deck)

    assert "Your last run" in body and "Simulate again" in body
    assert newest.get_absolute_url() in body
    assert older.get_absolute_url() in body, "still listed under the earlier runs"
    assert "2,000 games" in body
    assert STALE not in body
    assert "A run is going" not in body, "a failed run is not going"


def test_a_changed_deck_says_so(client, deck):
    _run(deck)
    deck.entries.exclude(oracle_card=deck.commander).first().delete()

    assert STALE in _page(client, deck)


def test_a_run_from_before_the_fingerprint_does_not_guess(client, deck):
    _run(deck, deck_print="")
    deck.entries.exclude(oracle_card=deck.commander).first().delete()

    body = _page(client, deck)

    assert "Your last run" in body
    assert STALE not in body


def test_a_run_still_going_shows_its_progress_first(client, deck):
    done = _run(deck)
    going = _run(deck, status=SimulationRun.Status.RUNNING)

    body = _page(client, deck)

    assert "A run is going" in body
    assert going.get_absolute_url() in body
    assert body.index("A run is going</p>") < body.index("Your last run</p>")
    assert done.get_absolute_url() in body
