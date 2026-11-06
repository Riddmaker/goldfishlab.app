"""Phase 11 C: what others are doing, on the home page (K11, K12, P8).

What these tests hold:

1. **A line names nobody.** "Another user" or "Another guest", the commander
   and nothing else: never an email, never a deck name, never a time.
2. **Never the viewer's own,** whether the viewer is a user or a guest. An
   anonymous visitor sees everything.
3. **Newest first, forty at most,** across runs, playtests, imports and new
   accounts; failed runs and pending imports are not news. The page shows
   eight and loops the rest.
4. **One build a minute.** The merged list comes from the cache, so a visit
   costs no query for it.
5. **The page:** a band of its own with its heading, the empty state, a poll
   every 30 s, a Pause button that only shows with its script, and a word about
   it in the privacy policy.
6. **Fun lines (Paket 4, PR 16)** after every seven events and never sooner,
   each different, set apart for eyes and screen readers; pop culture stays
   English, ours are translated. The loop's merge rule (static/js/ticker.js)
   runs here under Node.
"""

import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone, translation

from core import ticker
from core.templatetags import icons
from decks import services as deck_services
from decks.models import DeckImport
from playtest.models import PlaytestSession
from simulations.models import SimulationRun
from tests.test_i18n import GERMAN

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"
QUIET = "Quiet right now. The lab is waiting for your deck."
TICKER_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "ticker.js"
NODE = shutil.which("node")


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
    return [line.text for line in ticker.lines_for(user or AnonymousUser())
            if line.kind != ticker.FUN]


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


def test_the_labs_own_account_is_not_news(catalogue):
    # The precons' runs belong to the site's own account (`is_system`); a
    # batch of them would fill every line.
    lab = User.objects.create_user(email="lab@example.com", password="pw-test-1234",
                                   is_system=True)
    deck = _deck(lab)
    _run(deck)
    PlaytestSession.objects.create(deck=deck, owner=lab, seed=1)

    owners = {event.owner_id for event in ticker.lines_for(AnonymousUser())}

    assert lab.pk not in owners


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


def test_newest_first_every_one_and_a_fun_line_after_seven(catalogue):
    deck = _deck(_user("dave@example.com"))
    _quiet_the_rest()
    for minutes in range(12):
        run = _run(deck, minutes_ago=minutes)
        SimulationRun.objects.filter(pk=run.pk).update(games_total=1000 + minutes)

    lines = ticker.lines_for(AnonymousUser())
    runs = [line.text for line in lines if line.kind == ticker.RUN]

    assert len(runs) == 12
    assert runs[0].endswith("1,000 games.")
    assert runs[-1].endswith("1,011 games.")
    assert [line.kind == ticker.FUN for line in lines].index(True) == 7, "the eighth place"


def test_the_list_is_built_once_and_then_read_from_the_cache(
    catalogue, django_assert_num_queries,
):
    me = _user("erin@example.com")
    _run(_deck(_user("frank@example.com")))
    _texts()

    with django_assert_num_queries(0):
        assert _texts(me)


def test_the_home_page_has_the_ticker_as_a_band(client, catalogue):
    deck = _deck(_user("gina@example.com"))
    _run(deck)

    body = client.get(reverse("home")).content.decode()

    assert 'class="ticker-band' in body
    assert "What others did recently" in body
    assert "icon-simulation" in body, "each line has its symbol"
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


# --- fun lines (Paket 4, PR 16) ---------------------------------------------


def _events(count):
    now = timezone.now()
    return [ticker.Event(f"k{n}", n + 1, ticker.RUN, now - timedelta(minutes=n), games=1)
            for n in range(count)]


@pytest.mark.parametrize("count", [0, 3, 6, 7, 8, 14, 20, 35, 40])
def test_a_fun_line_comes_after_every_seven_events_and_never_sooner(count):
    lines = ticker.with_fun(_events(count))
    fun = [place for place, line in enumerate(lines) if line.kind == ticker.FUN]

    events = [line.key for line in lines if line.kind != ticker.FUN]
    assert events == [f"k{n}" for n in range(count)]
    assert len(fun) == count // 7
    assert fun == [8 * n + 7 for n in range(len(fun))]
    assert len({lines[place].key for place in fun}) == len(fun), "each one different"


def test_a_quiet_site_has_no_fun_lines():
    assert all(line.kind != ticker.FUN for line in ticker.with_fun(_events(6)))


def test_every_line_has_a_drawn_symbol():
    kinds = {ticker.RUN, ticker.PLAYTEST, ticker.IMPORT, ticker.JOIN}
    assert set(ticker.ICONS) == kinds
    assert set(ticker.ICONS.values()) <= icons.NAMES
    assert {line.icon for line in ticker.FUN_LINES} <= icons.NAMES
    assert len(ticker.FUN_LINES) == 10


def test_a_fun_line_belongs_to_nobody():
    assert all(line.owner_id is None for line in ticker.FUN_LINES)


def test_pop_culture_stays_english_and_ours_are_translated():
    base = next(line for line in ticker.FUN_LINES if line.name == "base")
    ours = next(line for line in ticker.FUN_LINES if line.name == "never-blocks")

    with translation.override("de"):
        assert base.text == "All your base are belong to us."
        assert base.lang == "en"
        assert ours.text == "Der Goldfisch blockt nie."
        assert ours.lang == ""
    with translation.override("en"):
        assert base.lang == "", "no lang where the page is English already"


def test_a_fun_line_is_marked_as_the_fish_speaking(client, catalogue, settings):
    settings.LANGUAGES = GERMAN
    deck = _deck(_user("ivy@example.com"))
    _quiet_the_rest()
    for minutes in range(7):
        _run(deck, minutes_ago=minutes)

    client.cookies["django_language"] = "de"
    body = client.get(reverse("ticker")).content.decode()
    fun = body[body.index("data-fun"):]
    fun = fun[:fun.index("</li>")]

    assert 'class="sr-only">Der Fisch sagt:' in fun
    assert 'class="icon icon-fun-' in fun


# --- the loop's merge rule, under Node ---------------------------------------

MERGE = """
const ticker = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map(([a, b]) => ticker.merge(a, b).map((l) => l.key))));
"""


def _lines(spec):
    """'a b F c' -> [{key: a}, {key: b}, {key: F, fun}, ...]; capitals are fun lines."""
    return [{"key": key, "fun": key.isupper()} for key in spec.split()]


def _merge(*cases):
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - our own script and file, fixed arguments
        [NODE, "-e", MERGE, str(TICKER_JS),
         json.dumps([[_lines(a), _lines(b)] for a, b in cases])],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return [" ".join(keys) for keys in json.loads(result.stdout)]


def test_a_new_event_comes_in_at_the_top_and_the_loop_goes_on():
    # Mid-loop: e and f were moved round to the top. n is new.
    assert _merge(("e f a b", "n a b e f")) == ["n e f a b"]


def test_an_event_no_longer_sent_leaves_the_loop():
    assert _merge(("c a b", "a b")) == ["a b"]


def test_fun_lines_stay_where_they_are_whatever_the_answer_picks():
    loop = "a b c d e f g X h i j k l m n Y o"
    answer = "a b c d e f g Z h i j k l m n W o"
    assert _merge((loop, answer)) == [loop]


def test_a_fun_line_that_would_come_too_soon_goes():
    # Two events left the loop between X and Y: Y would follow five events.
    loop = "a b c d e f g X h i j k l m n Y"
    answer = "a b c d e f g h i j k l"
    assert _merge((loop, answer)) == ["a b c d e f g X h i j k l"]
