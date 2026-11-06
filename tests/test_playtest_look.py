"""The board's look (Paket 4, PR 19): one table for free and strict.

What these tests hold:

1. **The two tables are the same.** Felt with the hand on it, the journal as a
   page beside it; strict adds only what its rules need (the phases, Next
   phase, Mulligan and Keep), free only its own moves.
2. **Strict writes the engine's log into the journal**, cut at the turns the
   engine opened, in English like the card names in it.
3. **A tapped permanent is marked**, read off its zone.
4. **Only the newest line inks in.**
"""

import pytest
from django.contrib.auth import get_user_model

from decks import seeding
from decks.fixtures import LAND_LIGHT
from playtest import services, views
from playtest.models import PlaytestSession
from simulation import actions

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="look@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(owner):
    return seeding.build(LAND_LIGHT, owner).deck


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def _page(client, session):
    return client.get(session.get_absolute_url()).content.decode()


def _play(session, kind, **payload):
    return services.record(session, actions.from_row(kind, payload))


def _first_land(session):
    return next(index for index, card in enumerate(services.state(session).hand)
                if card.is_land)


def _strict_turn_one(deck, owner):
    """A strict game in turn one, with a land out."""
    session = services.start(deck, owner, seed=4242)
    _play(session, "keep_hand")
    _play(session, "advance_phase")
    _play(session, "play_land", index=_first_land(session))
    return session


# --- the strict log as the journal's turns -----------------------------------

def test_the_log_is_cut_at_the_turns_the_engine_opened():
    turns = views.log_turns(["Mulligan to 6", "--- Turn 1 ---", "Land: Swamp",
                             "--- Turn 2 ---", "  -> Card drawn"])
    assert [turn.number for turn in turns] == [0, 1, 2]
    assert [line["text"] for line in turns[1].lines] == ["Land: Swamp"]
    assert turns[2].lines[0]["text"] == "-> Card drawn"


def test_without_a_mulligan_there_is_no_opening_turn():
    turns = views.log_turns(["--- Turn 1 ---", "Land: Swamp"])
    assert [turn.number for turn in turns] == [1]


def test_an_empty_log_has_no_turns():
    assert views.log_turns([]) == []


# --- tapped ----------------------------------------------------------------

def test_a_tapped_permanent_is_marked_and_the_rest_are_not(deck, owner):
    session = _strict_turn_one(deck, owner)
    game = _play(session, "tap_permanent", zone=actions.LANDS)
    tiles = views._tiles(game.lands, {})
    assert [tile["tapped"] for tile in tiles] == [True] * len(game.lands)
    assert not any(tile["tapped"] for tile in views._tiles(game.hand, {}))


def test_the_board_lays_a_tapped_land_on_its_side(signed_in, deck, owner):
    session = _strict_turn_one(deck, owner)
    assert "data-tapped" not in _page(signed_in, session)
    _play(session, "tap_permanent", zone=actions.LANDS)
    page = _page(signed_in, session)
    assert page.count("data-tapped") == 1
    assert "(tapped)" in page


# --- one table for both modes ------------------------------------------------

def _layout(page):
    """Where the table, the hand on it and the journal sit in the page."""
    return [page.index(marker) for marker in
            ('class="playtest-felt"', 'class="playtest-hand"', 'class="journal-page"')]


@pytest.mark.parametrize("mode", [PlaytestSession.FREE, PlaytestSession.STRICT])
def test_both_modes_lay_the_hand_on_the_felt_and_the_journal_beside_it(
        signed_in, deck, owner, mode):
    session = services.start(deck, owner, seed=4242, mode=mode)
    page = _page(signed_in, session)
    felt, hand, journal = _layout(page)
    assert felt < hand < journal
    assert "data-journal" in page
    assert "data-mode-switch" in page
    assert "What happened" not in page


def test_strict_alone_shows_the_phases_and_its_moves(signed_in, deck, owner):
    strict = _strict_turn_one(deck, owner)
    page = _page(signed_in, strict)
    assert 'aria-current="step"' in page
    assert 'value="advance_phase"' in page
    assert "data-free-moves" not in page

    free_page = _page(signed_in, services.start(deck, owner, seed=4242,
                                                mode=PlaytestSession.FREE))
    assert 'aria-current="step"' not in free_page
    assert 'value="advance_phase"' not in free_page


def test_a_strict_opening_offers_mulligan_and_keep(signed_in, deck, owner):
    page = _page(signed_in, services.start(deck, owner, seed=4242))
    assert 'value="mulligan"' in page
    assert 'value="keep_hand"' in page


def test_strict_writes_the_engine_log_into_the_journal_in_english(signed_in, deck, owner):
    page = _page(signed_in, _strict_turn_one(deck, owner))
    journal = page[page.index('class="journal-page"'):]
    assert "Turn 1" in journal
    assert 'lang="en"' in journal


def test_only_the_newest_line_inks_in(signed_in, deck, owner):
    session = services.start(deck, owner, seed=4242, mode=PlaytestSession.FREE)
    _play(session, "draw", count=1)
    page = _page(signed_in, session)
    assert page.count("journal-new") == 1
    assert page.index("journal-new") > page.rindex("data-journal-turn")
