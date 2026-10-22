"""Engine v34: land searches the reader called conditional.

Only one of the 37 had a condition: Edge of Autumn's "four or fewer lands".
The others were moments or wordings the reader did not know:

1. **Read now**: a Saga's second chapter, the turn after it enters (Binding
   the Old Gods); "When you cast this spell" (Sowing Mycospawn); a Room's
   "When you unlock this door"; the card's own name ("When Casal enters");
   an ability word (Exploding Borders' "Domain —"); "a land or Lander"
   (Larval Scoutlander).
2. **Named for what they are**: a search on the card's other face, in one
   mode of a modal spell, as a card dies - still not played.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _land_search
from simulation import actions, build
from simulation.cards import ENCHANTMENT, LAND, SORCERY, DeckDefinition, LandSearch
from simulation.game import Game
from simulation.manacost import parse
from simulations.engine.adapter import _land_search_text
from tests.engine_builders import tap

Kind = DerivedProfile.Kind


def read(text, kind, name="Card"):
    return _land_search(OracleCard(name=name, front_name=name, oracle_text=text), kind)


# --- the reader -----------------------------------------------------------------------


def test_edge_of_autumn_asks_how_many_lands():
    spec = read("If you control four or fewer lands, search your library for a basic land card, "
                "put it onto the battlefield tapped, then shuffle.\nCycling—Sacrifice a land.",
                Kind.SORCERY).spec
    assert (spec["when"], spec["lands_at_most"], spec["tapped"]) == ("cast", 4, True)


@pytest.mark.parametrize("text, kind, name, when", [
    ("(As this Saga enters and after your draw step, add a lore counter.)\nI — Destroy target "
     "nonland permanent an opponent controls.\nII — Search your library for a Forest card, put it "
     "onto the battlefield tapped, then shuffle.\nIII — Creatures you control gain deathtouch.",
     Kind.ENCHANTMENT, "Binding the Old Gods", "chapter2"),
    ("When you cast this spell, search your library for a land card, put it onto the "
     "battlefield, then shuffle.", Kind.CREATURE, "Sowing Mycospawn", "cast"),
    ("When you unlock this door, search your library for a basic land card, put it onto the "
     "battlefield tapped, then shuffle.", Kind.ENCHANTMENT, "Moldering Gym", "enters"),
    ("Vigilance\nWhen Casal enters, search your library for a Forest card, put it onto the "
     "battlefield tapped, then shuffle.", Kind.CREATURE, "Casal, Lurkwood Pathfinder", "enters"),
    ("Domain — Search your library for a basic land card, put that card onto the battlefield "
     "tapped, then shuffle.", Kind.SORCERY, "Exploding Borders", "cast"),
])
def test_a_moment_the_reader_did_not_know(text, kind, name, when):
    found = read(text, kind, name)
    assert found.spec["when"] == when and not found.reason


def test_larval_scoutlander_sacrifices_a_land_first():
    spec = read("When this Spacecraft enters, you may sacrifice a land or Lander. If you do, "
                "search your library for up to two basic land cards, put them onto the "
                "battlefield tapped, then shuffle.", Kind.ARTIFACT).spec
    assert (spec["battlefield"], spec["sacrifices_land"]) == (2, True)


def test_two_team_lines_are_a_choice_not_an_ability_word():
    found = read("Team Cloudspire — Create two 1/1 white Warrior creature tokens.\nTeam Speed "
                 "Demons — Search your library for a basic land card, put it onto the "
                 "battlefield tapped, then shuffle.", Kind.SORCERY)
    assert found.spec is None and "condition" in found.reason


@pytest.mark.parametrize("text, kind, reason", [
    ("Beanstalk Giant's power and toughness are each equal to the number of lands you "
     "control.\n//\nSearch your library for a basic land card, put it onto the battlefield, then "
     "shuffle.", Kind.CREATURE, "other face"),
    ("Choose two —\n• Exile target player's graveyard.\n• Search your library for up to two "
     "land cards, put them onto the battlefield tapped, then shuffle.", Kind.SORCERY, "one mode"),
    ("When this creature dies, you may search your library for a basic land card, put it onto "
     "the battlefield tapped, then shuffle.", Kind.CREATURE, "dies"),
])
def test_what_is_not_played_says_what_it_is(text, kind, reason):
    found = read(text, kind)
    assert found.spec is None and reason in found.reason


# --- the engine -------------------------------------------------------------------------


def land(name, subtype):
    return build.card(name, 0, 0, 0, LAND, subtypes=frozenset({subtype}), basic=True,
                      types=frozenset({"land"}))


FOREST, SWAMP = land("Forest", "forest"), land("Swamp", "swamp")
EDGE = build.card("Edge of Autumn", 2, 0, 2, SORCERY, cost=parse("{2}"),
                  land_search=LandSearch(lands_at_most=4))
BINDING = build.card("Binding the Old Gods", 4, 0, 4, ENCHANTMENT, cost=parse("{4}"),
                     land_search=LandSearch(basic=False, types=frozenset({"forest"}),
                                            when="chapter2"))


def game_with(lands, hand, library):
    game = Game(random.Random(1), deck=DeckDefinition("v34", None, (FOREST,)))
    game.lands, game.hand, game.library = list(lands), list(hand), list(library)
    tap(game, lands=0)
    game.turn = 3
    return game


@pytest.mark.parametrize("lands, found", [(4, 1), (5, 0)])
def test_edge_of_autumn_finds_a_land_only_with_four_or_fewer(lands, found):
    game = game_with([SWAMP] * lands, [EDGE], [FOREST])
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert len(game.lands) == lands + found


def test_binding_the_old_gods_finds_its_forest_the_next_turn():
    game = game_with([SWAMP] * 4, [BINDING], [SWAMP, FOREST, SWAMP])
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert len(game.lands) == 4, "chapter I only, as it enters"
    game.begin_turn()
    assert FOREST in game.lands and game.tapped_lands == 1
    game.begin_turn()
    assert len(game.lands) == 5, "chapter II once"


def test_the_card_page_says_when_and_how_many():
    assert _land_search_text(EDGE.play.search).endswith(", if you control 4 lands or fewer")
    assert _land_search_text(BINDING.play.search).startswith(
        "on its second chapter, the turn after it enters: ")
