"""P19 R10 (engine version 14): tutors that put the card onto the battlefield.

Green Sun's Zenith, Chord of Calling, Finale of Devastation, Nature's Rhythm
and Whir of Invention were gaps: the engine could put a found card in the hand
or the graveyard and nowhere else. They now put it onto the battlefield, mana
value X or less, X being what the spell was cast for (R9).
"""

import random

import pytest

from cards.models import OracleCard
from cards.profiles import _tutor
from simulation import actions
from simulation.cards import CREATURE, LAND, ROCK, SORCERY, Card, LandSearch, TutorSpec
from simulation.game import Game
from simulation.manacost import parse

TAGS = {"tutor", "tutor-creature", "tutor-to-battlefield", "tutor-mv"}

# --- the reader -----------------------------------------------------------------


def oracle(text, cost="{X}{G}"):
    return OracleCard(name="Card", front_name="Card", type_line="Sorcery", mana_cost=cost,
                      oracle_text=text)


@pytest.mark.parametrize("text, kind, color, max_mv", [
    ("Search your library for a green creature card with mana value X or less, put it onto "
     "the battlefield, then shuffle.", "creature", "G", "X"),
    ("Search your library and/or graveyard for a creature card with mana value X or less "
     "and put it onto the battlefield.", "creature", "", "X"),
    ("Search your library for an artifact card with mana value X or less, put it onto the "
     "battlefield, then shuffle.", "artifact", "", "X"),
])
def test_a_search_onto_the_battlefield_is_read_whole(text, kind, color, max_mv):
    tutor = _tutor(oracle(text), TAGS)
    assert (tutor.zone, tutor.count, tutor.kind, tutor.reason) == ("battlefield", 1, kind, "")
    assert tutor.filter == {"color": color, "max_mv": max_mv}


def test_an_ability_that_searches_is_not_what_the_card_does_when_cast():
    tezzeret = oracle("+1: Untap up to two target artifacts.\n−X: Search your library for an "
                      "artifact card with mana value X or less, put it onto the battlefield, "
                      "then shuffle.", cost="{3}{U}{U}")
    tutor = _tutor(tezzeret, TAGS | {"tutor-artifact"})
    assert tutor.filter is None and "cannot do" in tutor.reason


# --- the engine -----------------------------------------------------------------


FOREST = Card("Forest", 0, 0, 0, LAND, subtypes=frozenset({"forest"}), basic=True)
ZENITH = Card("Green Sun's Zenith", 1, 0, 0, SORCERY, cost=parse("{X}{G}"), x_count=1,
              tutor=TutorSpec(to_hand=False, to_battlefield=True, kind="creature",
                              color="G", max_mv_x=True))
WHIR = Card("Whir of Invention", 3, 0, 0, SORCERY, cost=parse("{X}{U}{U}{U}"), x_count=1,
            tutor=TutorSpec(to_hand=False, to_battlefield=True, kind="artifact", max_mv_x=True))
ELVES = Card("Wood Elves", 3, 0, 2, CREATURE, cost=parse("{2}{G}"),
             types=frozenset({"creature"}),
             land_search=LandSearch(battlefield=1, tapped=False, types=frozenset({"forest"}),
                                    when="enters"))
TITAN = Card("Primeval Titan", 6, 0, 4, CREATURE, cost=parse("{4}{G}{G}"),
             types=frozenset({"creature"}))
GOLEM = Card("Golem", 3, 0, 3, CREATURE, cost=parse("{3}"), types=frozenset({"creature"}))
SIGNET = Card("Signet", 2, 0, 2, ROCK, cost=parse("{2}"), types=frozenset({"artifact"}))


def game_with(lands, hand, library=()):
    game = Game(random.Random(1))
    game.lands, game.hand, game.library = list(lands), list(hand), list(library)
    game.tapped_lands = 0
    return game


def cast(game, card, x):
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=game.hand.index(card), x=x).run(game, None)


def test_zenith_finds_a_green_creature_of_x_or_less_and_it_enters():
    game = game_with([FOREST] * 4, [ZENITH], library=[TITAN, GOLEM, ELVES, FOREST])
    cast(game, ZENITH, 3)
    assert ELVES in game.creatures, "the only green creature of mana value 3 or less"
    assert TITAN in game.library and GOLEM in game.library
    assert len(game.lands) == 5, "Wood Elves still fetch their Forest"


def test_nothing_small_enough_finds_nothing():
    game = game_with([FOREST] * 2, [ZENITH], library=[TITAN])
    cast(game, ZENITH, 1)
    assert game.creatures == [] and game.library == [TITAN]


def test_whir_finds_a_mana_rock_too():
    island = Card("Island", 0, 0, 0, LAND, subtypes=frozenset({"island"}), basic=True)
    game = game_with([island] * 5, [WHIR], library=[SIGNET, GOLEM])
    cast(game, WHIR, 2)
    assert SIGNET in game.rocks
