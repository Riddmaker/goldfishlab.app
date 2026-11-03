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
from django.urls import reverse

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
    assert run.list_print == summary.list_fingerprint(deck)


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
    assert body.index("A run is going</span>") < body.index("Your last run</a>")
    assert done.get_absolute_url() in body


# --- The deck page's first row (Themen 2026-11) ---------------------------------


def test_the_last_report_is_the_first_button(client, deck):
    """Somebody coming back pressed "Simulate" because the link was easy to miss."""
    _run(deck, games=1000)
    newest = _run(deck, games=2000)

    body = _page(client, deck)

    link = body.index("data-last-run-link")
    assert newest.get_absolute_url() in body[link - 200:link + 200]
    assert "Open the last report" in body
    assert link < body.index("Simulate again")


def test_a_run_going_is_the_first_button(client, deck):
    going = _run(deck, status=SimulationRun.Status.RUNNING)

    body = _page(client, deck)

    link = body.index("data-last-run-link")
    assert going.get_absolute_url() in body[link - 200:link + 200]
    assert "Watch the run" in body


def test_without_a_run_simulate_is_the_first_button(client, deck):
    assert "data-last-run-link" not in _page(client, deck)


def test_one_first_draw_choice_for_both_buttons(client, deck):
    """The playtest's own checkbox beside the dropdown read as the same question twice."""
    body = _page(client, deck)

    assert 'id="deal-hand"' not in body
    assert 'type="checkbox" name="on_the_play"' not in body
    assert body.count('name="on_the_play"') == 1
    assert f'formaction="{reverse("playtest:start", args=[deck.id])}"' in body
    assert "Simulate plays the deck out" not in body


def test_combos_stand_open_above_the_cards(client, deck):
    body = _page(client, deck)

    assert '<details id="combos"' not in body
    assert body.index('id="combos"') < body.index('id="cards"')


def test_the_commanders_name_opens_the_choice(client, deck):
    body = _page(client, deck)

    summary = body[body.index('class="commander-toggle'):]
    summary = summary[:summary.index("</summary>")]
    assert deck.commander.front_name in summary
