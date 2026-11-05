"""Free mode (Paket 4, PR 18): the board as a kitchen table.

What these tests hold:

1. **Nothing is blocked, nothing is paid.** A card goes where it belongs and
   the life, the pool and the engine's mechanics stay out of it.
2. **A new hand is a move.** It deals again in the same session, and undo
   brings the hand before it back - also from a cold replay.
3. **A session keeps its mode.** A free game refuses the engine's phases, a
   strict one the free moves; switching deals the same shuffle again.
4. **The journal guesses, and says so.** Pure, so its rules are tested without
   a database: a second land or a draw past the turn's one is a new turn until
   the player says "next turn"; a card that costs more than the sources out is
   an unusual move.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from decks import seeding
from decks.fixtures import LAND_LIGHT
from playtest import free, services
from playtest.models import PlaytestSession
from simulation import actions

pytestmark = pytest.mark.django_db

User = get_user_model()
HTMX = {"HX-Request": "true"}
KEYS_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "playtest-keys.js"
NODE = shutil.which("node")


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="free@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(owner):
    return seeding.build(LAND_LIGHT, owner).deck


@pytest.fixture
def session(deck, owner):
    return services.start(deck, owner, seed=4242, mode=PlaytestSession.FREE)


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def act(client, session, **data):
    return client.post(reverse("playtest:act", args=[session.id]), data, headers=HTMX)


def _index(game, test):
    return next(index for index, card in enumerate(game.hand) if test(card))


def _cold(session):
    """The game replayed from the actions alone, no cache."""
    session.invalidate()
    session.save()
    return services.state(session)


# --- the journal, pure ---------------------------------------------------------

def _turns(book):
    return [(turn.number, turn.guessed) for turn in book.turns]


def _texts(book):
    return [line["text"] for turn in book.turns for line in turn.lines]


LAND = free.Step(free.LAND, ("Swamp",))


def _draw(name="Card"):
    return free.Step(free.DRAW, (name,))


def test_a_fresh_hand_is_turn_one_and_not_a_guess():
    book = free.journal([], on_the_play=True)
    assert _turns(book) == [(1, False)]
    assert _texts(book) == ["Seven cards dealt."]
    assert book.unusual == 0


def test_on_the_play_the_first_draw_starts_a_guessed_second_turn():
    book = free.journal([LAND, _draw("Sol Ring")], on_the_play=True)
    assert _turns(book) == [(1, False), (2, True)]
    assert "Drew Sol Ring." in _texts(book)


def test_without_skipping_the_first_draw_belongs_to_turn_one():
    book = free.journal([_draw(), LAND, _draw()], on_the_play=False)
    assert _turns(book) == [(1, False), (2, True)]


def test_a_second_land_is_a_guessed_new_turn_not_a_mistake():
    book = free.journal([LAND, LAND, _draw()], on_the_play=True)
    # The second land opened turn 2, whose draw is the next one.
    assert _turns(book) == [(1, False), (2, True)]
    assert book.unusual == 0


def test_once_the_player_says_next_turn_the_turns_are_trusted():
    turn = free.Step(free.TURN, ("Island",))
    book = free.journal([LAND, turn, LAND, LAND, _draw()], on_the_play=True)
    assert _turns(book) == [(1, False), (2, False)]
    assert book.unusual == 1
    unusual = [line for line in book.now.lines if line["unusual"]]
    assert unusual[0]["text"] == "A second land this turn."
    assert "Drew Island." in _texts(book)


def test_a_card_that_costs_more_than_the_sources_out_is_unusual():
    rhystic = free.Step(free.PLAY, ("Rhystic Study",), cost=3, sources=1)
    cheap = free.Step(free.PLAY, ("Sol Ring",), cost=1, sources=1)
    book = free.journal([cheap, rhystic], on_the_play=True)
    assert book.unusual == 1
    assert "Rhystic Study costs 3, with 1 mana source out." in _texts(book)


def test_a_commander_counts_its_tax():
    commander = free.Step(free.COMMANDER, ("Atraxa",), cost=6, sources=2)
    book = free.journal([commander], on_the_play=True)
    assert "Commander: Atraxa." in _texts(book)
    assert "Atraxa costs 6, with 2 mana sources out." in _texts(book)


def test_a_new_hand_starts_the_journal_again():
    rhystic = free.Step(free.PLAY, ("Rhystic Study",), cost=3, sources=0)
    book = free.journal([LAND, LAND, rhystic, free.Step(free.DEAL)], on_the_play=True)
    assert _turns(book) == [(1, False)]
    assert _texts(book) == ["New hand: seven fresh cards."]
    assert book.unusual == 0


def test_an_empty_library_is_said():
    book = free.journal([free.Step(free.DRAW)], on_the_play=False)
    assert "The library is empty." in _texts(book)


# --- the moves -----------------------------------------------------------------

def test_a_free_play_puts_each_card_where_it_belongs_and_pays_nothing(session):
    game = services.state(session)
    life = game.life
    played = []
    while game.hand:
        card = game.hand[0]
        game = services.record(session, free.FreePlay(index=0))
        played.append((card, free.zone_for(card)))
    for card, zone in played:
        assert card in actions.zone_of(game, zone)
    assert game.life == life
    assert game.pool is None
    assert any(zone == actions.LANDS for _, zone in played)


def test_a_new_hand_deals_again_and_undo_brings_the_old_one_back(session):
    first = [card.name for card in services.state(session).hand]
    services.record(session, free.NewHand(seed=99))
    second = [card.name for card in services.state(session).hand]
    assert len(second) == 7
    assert second != first
    assert [card.name for card in _cold(session).hand] == second
    services.undo(session)
    assert [card.name for card in _cold(session).hand] == first


def test_next_turn_untaps_and_draws(session):
    game = services.state(session)
    library = len(game.library)
    game = services.record(session, free.NextTurn())
    assert len(game.library) == library - 1
    assert len(game.hand) == 8
    assert game.turn == 2


def test_the_commander_comes_out_once(session):
    if not services.state(session).deck.commanders:
        pytest.skip("the fixture deck has no commander")
    game = services.record(session, free.FreeCommander(index=0))
    assert game.has(game.deck.commanders[0].name)
    assert game.commander_casts == 1
    with pytest.raises(actions.IllegalAction, match="already"):
        services.record(session, free.FreeCommander(index=0))


def test_a_session_keeps_its_mode(deck, owner, session):
    strict = services.start(deck, owner, seed=4242)
    assert strict.mode == PlaytestSession.STRICT
    with pytest.raises(actions.IllegalAction, match="not a move of this mode"):
        services.record(strict, free.FreePlay(index=0))
    with pytest.raises(actions.IllegalAction, match="not a move of this mode"):
        services.record(session, actions.AdvancePhase())
    services.record(session, actions.Draw())
    assert session.actions.count() == 1


def test_a_branch_keeps_the_mode(session):
    services.record(session, free.FreePlay(index=0))
    assert services.fork(session, 1).mode == PlaytestSession.FREE


def test_the_journal_reads_the_cards_that_were_played(session):
    game = services.state(session)
    land = game.hand[_index(game, lambda card: card.is_land)]
    services.record(session, free.FreePlay(index=_index(game, lambda card: card.is_land)))
    services.record(session, actions.Draw())
    book = services.journal(session)
    texts = _texts(book)
    assert f"Land: {land.name}." in texts
    assert any(text.startswith("Drew ") for text in texts)
    assert _turns(book) == [(1, False), (2, True)]


# --- over HTTP -----------------------------------------------------------------

def test_a_hand_dealt_from_the_deck_page_is_free(signed_in, deck):
    signed_in.post(reverse("playtest:start", args=[deck.id]), {"on_the_play": "1"})
    assert PlaytestSession.objects.get(deck=deck).mode == PlaytestSession.FREE


def test_asking_for_strict_deals_a_strict_game(signed_in, deck):
    signed_in.post(reverse("playtest:start", args=[deck.id]), {"mode": "strict"})
    assert PlaytestSession.objects.get(deck=deck).mode == PlaytestSession.STRICT


def test_the_free_board_offers_the_free_moves_and_no_mulligan(signed_in, session):
    page = signed_in.get(session.get_absolute_url()).content.decode()
    assert "data-free-moves" in page
    assert "data-journal" in page
    assert 'value="mulligan"' not in page
    assert 'value="advance_phase"' not in page
    for key in ("d", "n", "t", "ctrl+z"):
        assert f'data-key="{key}"' in page
    assert "js/playtest-keys.js" in page
    posted = {line.split('value="')[1].split('"')[0]
              for line in page.splitlines()
              if 'name="kind"' in line and 'value="' in line}
    assert posted <= free.ALLOWED
    assert "free_play" in posted


def test_a_free_board_refuses_a_strict_move_and_a_strict_board_a_free_one(
        signed_in, deck, owner, session):
    assert act(signed_in, session, kind="advance_phase").status_code == 400
    strict = services.start(deck, owner, seed=1)
    assert act(signed_in, strict, kind="free_play", index=0).status_code == 400
    assert not strict.actions.exists()


def test_the_seed_of_a_new_hand_is_never_taken_from_the_post(signed_in, session):
    act(signed_in, session, kind="new_hand", seed="7")
    stored = session.actions.get().payload["seed"]
    assert stored != 7
    assert stored > 0


def test_an_unusual_move_shows_in_the_journal(signed_in, session):
    game = services.state(session)
    spell = _index(game, lambda card: not card.is_land and card.mv > 0)
    response = act(signed_in, session, kind="free_play", index=spell)
    page = response.content.decode()
    assert response.status_code == 200
    assert "data-unusual" in page
    assert "Unusual move:" in page
    assert "data-unusual-count" in page


def test_switching_mode_deals_the_same_shuffle_in_the_other_mode(signed_in, deck, session):
    page = signed_in.get(session.get_absolute_url()).content.decode()
    assert "data-mode-switch" in page
    assert f'name="seed" value="{session.seed}"' in page
    response = signed_in.post(reverse("playtest:start", args=[deck.id]),
                              {"mode": "strict", "seed": session.seed,
                               "on_the_play": "1" if session.on_the_play else "0"})
    strict = PlaytestSession.objects.exclude(pk=session.pk).get()
    assert response["Location"] == strict.get_absolute_url()
    assert strict.mode == PlaytestSession.STRICT
    assert ([card.name for card in services.state(strict).hand]
            == [card.name for card in services.state(session).hand])


def test_the_free_board_speaks_german(signed_in, session, settings):
    from tests.test_i18n import GERMAN
    settings.LANGUAGES = GERMAN
    signed_in.cookies["django_language"] = "de"
    page = signed_in.get(session.get_absolute_url()).content.decode()
    assert "Neue Hand" in page
    assert "Nächster Zug" in page
    assert "Journal" in page


# --- the keys ------------------------------------------------------------------

KEY_FOR = """
const { keyFor } = require(process.argv[1]);
console.log(JSON.stringify(JSON.parse(process.argv[2]).map(keyFor)));
"""


def _keys(*presses):
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - our own script and file, fixed arguments
        [NODE, "-e", KEY_FOR, str(KEYS_JS), json.dumps(list(presses))],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def test_the_keys_are_d_n_t_and_ctrl_z():
    assert _keys({"key": "d"}, {"key": "N"}, {"key": "t"},
                 {"key": "z", "ctrlKey": True}, {"key": "z", "metaKey": True}) == [
        "d", "n", "t", "ctrl+z", "ctrl+z"]


def test_no_key_while_typing_or_with_another_modifier():
    assert _keys({"key": "d", "tag": "INPUT"}, {"key": "d", "tag": "SELECT"},
                 {"key": "d", "editable": True}, {"key": "d", "ctrlKey": True},
                 {"key": "z", "ctrlKey": True, "shiftKey": True},
                 {"key": "d", "altKey": True}, {"key": "x"}) == [None] * 7


def test_switching_after_a_new_hand_deals_that_hand(signed_in, deck, session):
    services.record(session, free.NewHand(seed=31337))
    page = signed_in.get(session.get_absolute_url()).content.decode()
    assert 'name="seed" value="31337"' in page
    signed_in.post(reverse("playtest:start", args=[deck.id]), {"mode": "strict", "seed": 31337})
    strict = PlaytestSession.objects.exclude(pk=session.pk).get()
    assert ([card.name for card in services.state(strict).hand]
            == [card.name for card in services.state(session).hand])
