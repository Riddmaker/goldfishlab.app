"""Phase 11 C: what others are doing, on the home page (K11, K12, P8).

What these tests hold:

1. **A line names nobody.** "Another user" or "Another guest", the commander
   and nothing else: never an email, never a deck name, never a time.
2. **Never the viewer's own,** whether the viewer is a user or a guest. An
   anonymous visitor sees everything.
3. **Newest first, eight at most,** across runs, playtests, imports and new
   accounts; failed runs and pending imports are not news.
4. **One build a minute.** The merged list comes from the cache, so a visit
   costs no query for it.
5. **The page:** a third column with its caption, the empty state, a poll every
   30 s, a Pause button that only shows with its script, and a word about it
   in the privacy policy.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from core import ticker
from decks import services as deck_services
from decks.models import DeckImport
from playtest.models import PlaytestSession
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"
QUIET = "Quiet right now. The lab is waiting for your deck."


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.delete(ticker.CACHE_KEY)
    yield
    cache.delete(ticker.CACHE_KEY)


def _user(email, guest=False):
    return User.objects.create_user(email=email, password="pw-test-1234", is_guest=guest)


def _deck(owner, name="Secret deck name"):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name=name, filename="sample.csv",
    ).deck


def _run(deck, status=SimulationRun.Status.DONE, minutes_ago=0):
    return SimulationRun.objects.create(
        owner=deck.owner, deck=deck, games_total=2000, seed=1, status=status,
        finished_at=timezone.now() - timedelta(minutes=minutes_ago),
    )


def _texts(user=None):
    return [event.text for event in ticker.lines_for(user or AnonymousUser())]


def _quiet_the_rest():
    """Push imports and accounts far back, so a test sees only what it makes."""
    long_ago = timezone.now() - timedelta(days=30)
    DeckImport.objects.update(created_at=long_ago)
    User.objects.update(date_joined=long_ago)


def test_a_run_names_the_commander_and_nobody(catalogue):
    owner = _user("alice.secret@example.com")
    deck = _deck(owner)
    _run(deck)

    lines = _texts()
    run_line = next(line for line in lines if "simulated" in line)

    assert run_line == (
        f"Another user simulated a deck led by {deck.commander.name}: 2,000 games."
    )
    body = " ".join(lines)
    assert "alice" not in body and "Secret deck name" not in body


def test_a_guest_is_another_guest_and_is_not_a_new_user(catalogue):
    guest = _user("guest-1@guests.invalid", guest=True)
    deck = _deck(guest)
    PlaytestSession.objects.create(deck=deck, owner=guest, seed=1)

    lines = _texts()

    assert f"Another guest playtested a deck led by {deck.commander.name}." in lines
    assert f"Another guest imported a deck led by {deck.commander.name}." in lines
    assert "A new user joined." not in lines


def test_a_new_account_is_news_and_a_deck_without_a_commander_is_a_deck(catalogue):
    owner = _user("bob@example.com")
    deck = _deck(owner)
    deck.commander = None
    deck.save()

    lines = _texts()

    assert "A new user joined." in lines
    assert "Another user imported a deck." in lines


def test_the_viewer_never_sees_their_own(catalogue):
    me = _user("me@example.com")
    other = _user("other@example.com")
    _run(_deck(me))
    _run(_deck(other))

    mine = _texts(me)

    assert sum("simulated" in line for line in mine) == 1
    assert "A new user joined." in mine, "the other account, not mine"
    assert sum("simulated" in line for line in _texts()) == 2
    own_events = [e for e in ticker.lines_for(me) if e.owner_id == me.pk]
    assert own_events == []


def test_a_guest_never_sees_its_own_trial(catalogue):
    guest = _user("guest-2@guests.invalid", guest=True)
    _run(_deck(guest))

    assert _texts(guest) == []
    assert _texts() != []


def test_failed_runs_and_pending_imports_are_not_news(catalogue):
    deck = _deck(_user("carol@example.com"))
    _run(deck, status=SimulationRun.Status.FAILED)
    DeckImport.objects.update(status=DeckImport.Status.REVIEW)

    lines = _texts()

    assert not any("simulated" in line or "imported" in line for line in lines)


def test_newest_first_and_eight_at_most(catalogue):
    deck = _deck(_user("dave@example.com"))
    _quiet_the_rest()
    for minutes in range(12):
        run = _run(deck, minutes_ago=minutes)
        SimulationRun.objects.filter(pk=run.pk).update(games_total=1000 + minutes)

    lines = _texts()

    assert len(lines) == ticker.SHOWN
    assert lines[0].endswith("1,000 games.")
    assert lines[-1].endswith("1,007 games.")


def test_the_list_is_built_once_and_then_read_from_the_cache(
    catalogue, django_assert_num_queries,
):
    me = _user("erin@example.com")
    _run(_deck(_user("frank@example.com")))
    _texts()

    with django_assert_num_queries(0):
        assert _texts(me)


def test_the_home_page_has_the_ticker_as_its_third_column(client, catalogue):
    deck = _deck(_user("gina@example.com"))
    _run(deck)

    body = client.get(reverse("home")).content.decode()

    assert "md:grid-cols-3" in body
    assert "What others are doing" in body
    assert f"simulated a deck led by {deck.commander.name}" in body
    assert 'hx-trigger="every 30s"' in body
    assert f'hx-get="{reverse("ticker")}"' in body
    assert "js/ticker.js" in body
    pause = body[body.index("data-ticker-pause"):]
    assert "hidden" in pause[:pause.index(">")], "Pause shows only with its script"


def test_a_quiet_site_says_so(client, db):
    assert QUIET in client.get(reverse("home")).content.decode()


def test_the_poll_answers_with_the_list_alone(client, catalogue):
    me = _user("hank@example.com")
    _run(_deck(me))
    client.force_login(me)

    body = client.get(reverse("ticker")).content.decode()

    assert body.lstrip().startswith('<ul id="ticker-lines"')
    assert "<html" not in body
    assert 'hx-trigger="every 30s"' in body, "the answer keeps asking"
    assert "simulated" not in body, "my own run is not news to me"


def test_the_privacy_policy_says_what_the_home_page_shows(client, db):
    body = client.get(reverse("privacy")).content.decode()

    assert "On the home page" in body
    assert "Your own activity is never shown to you" in body
