"""Engine v34: more searches onto the battlefield.

Until v34 a search onto the battlefield was read for a creature or an
artifact card as a spell resolved (P19 R10). Now:

1. **More kinds**: an enchantment, a subtype (Godo's Equipment, Moggcatcher's
   Goblin), a nonlegendary card (Woodland Bellower), any permanent (Planar
   Bridge), and Neoform's creature of exactly one more mana value.
2. **As a creature enters**, with the card's name in the text ("When Godo
   enters"), and **as an ability** that costs mana and {T} alone - once a
   turn, a creature's not the turn it arrived.
3. **Not played**: a search in combat (Zur), as a card dies, a planeswalker's,
   a storm or epic spell's, one whose card is exiled, returned or attached
   later, one behind an additional cost the engine does not pay (Savage
   Order), and Wargate's permanent for X.
4. A basic land on top of the library (Embermouth Sentinel, Loam Larva).
"""

import json
import random

import pytest

from cards.models import OracleCard
from cards.profiles import _tutor
from simulation import actions, agent, build, serial
from simulation.cards import ARTIFACT, CREATURE, LAND, SORCERY, Card, DeckDefinition, TutorSpec
from simulation.game import Game
from simulation.manacost import parse
from simulations.engine.adapter import Reading
from tests.engine_builders import tap

TAGS = {"tutor", "tutor-to-battlefield"}


def oracle(name, text, *, type_line="Creature", cost="{3}"):
    return OracleCard(name=name, front_name=name, type_line=type_line, mana_cost=cost,
                      oracle_text=text)


def read(name, text, **kwargs):
    return _tutor(oracle(name, text, **kwargs), TAGS)


# --- the reader -----------------------------------------------------------------------


def test_godo_finds_an_equipment_as_he_enters():
    tutor = read("Godo, Bandit Warlord",
                 "When Godo enters, you may search your library for an Equipment card, put it "
                 "onto the battlefield, then shuffle.")
    assert (tutor.zone, tutor.kind, tutor.reason) == ("battlefield", "", "")
    assert tutor.filter == {"color": "", "max_mv": None, "types": ["equipment"]}


def test_woodland_bellower_finds_no_legendary_creature():
    tutor = read("Woodland Bellower",
                 "When this creature enters, you may search your library for a nonlegendary "
                 "green creature card with mana value 3 or less, put it onto the battlefield, "
                 "then shuffle.")
    assert tutor.kind == "creature"
    assert tutor.filter == {"color": "G", "max_mv": 3, "nonlegendary": True}


@pytest.mark.parametrize("text, filter_", [
    ("{3}, {T}: Search your library for a Goblin permanent card, put it onto the battlefield, "
     "then shuffle.",
     {"color": "", "max_mv": None, "types": ["goblin"], "activated": True, "cost": "{3}"}),
    ("{8}, {T}: Search your library for a permanent card, put it onto the battlefield, then "
     "shuffle.",
     {"color": "", "max_mv": None, "permanent": True, "activated": True, "cost": "{8}"}),
    ("{4}, {T}: Search your library for a Rebel permanent card with mana value 3 or less, put "
     "it onto the battlefield, then shuffle.",
     {"color": "", "max_mv": 3, "types": ["rebel"], "activated": True, "cost": "{4}"}),
])
def test_an_ability_for_mana_and_its_tap_is_read(text, filter_):
    assert read("Card", text).filter == filter_


def test_neoform_finds_exactly_one_more():
    tutor = read("Neoform", "As an additional cost to cast this spell, sacrifice a creature.\n"
                 "Search your library for a creature card with mana value equal to 1 plus the "
                 "sacrificed creature's mana value, put that card onto the battlefield with an "
                 "additional +1/+1 counter on it, then shuffle.", type_line="Sorcery")
    assert tutor.kind == "creature"
    assert tutor.filter == {"color": "", "max_mv": None, "plus_sacrificed": 1, "exact": True}


def test_rocco_still_finds_a_creature_for_x():
    tutor = read("Rocco, Cabaretti Caterer",
                 "When Rocco enters, if you cast it, you may search your library for a creature "
                 "card with mana value X or less, put it onto the battlefield, then shuffle.",
                 cost="{X}{R}{G}{W}")
    assert tutor.filter == {"color": "", "max_mv": "X"} and tutor.kind == "creature"


@pytest.mark.parametrize("name, text, type_line", [
    ("Zur the Enchanter", "Whenever Zur attacks, you may search your library for an enchantment "
     "card with mana value 3 or less, put it onto the battlefield, then shuffle.", "Creature"),
    ("Academy Rector", "When this creature dies, you may exile it. If you do, search your "
     "library for an enchantment card, put that card onto the battlefield, then shuffle.",
     "Creature"),
    ("Dragonstorm", "Search your library for a Dragon permanent card, put it onto the "
     "battlefield, then shuffle.\nStorm (When you cast this spell, copy it for each spell cast "
     "before it this turn.)", "Sorcery"),
    ("Enduring Ideal", "Search your library for an enchantment card, put it onto the "
     "battlefield, then shuffle.\nEpic (For the rest of the game, you can't cast spells.)",
     "Sorcery"),
    ("Stonehewer Giant", "{1}{W}, {T}: Search your library for an Equipment card, put it onto "
     "the battlefield, attach it to a creature you control, then shuffle.", "Creature"),
    ("Zirilan of the Claw", "{1}{R}{R}, {T}: Search your library for a Dragon permanent card, "
     "put that card onto the battlefield, then shuffle. That Dragon gains haste until end of "
     "turn. Exile it at the beginning of the next end step.", "Creature"),
    ("Kuldotha Forgemaster", "{T}, Sacrifice three artifacts: Search your library for an "
     "artifact card, put it onto the battlefield, then shuffle.", "Artifact Creature"),
    ("Savage Order", "As an additional cost to cast this spell, sacrifice a creature with power "
     "4 or greater.\nSearch your library for a Dinosaur creature card, put it onto the "
     "battlefield, then shuffle. It gains indestructible until your next turn.", "Sorcery"),
    ("Wargate", "Search your library for a permanent card with mana value X or less, put it "
     "onto the battlefield, then shuffle.", "Sorcery"),
    ("Light-Paws, Emperor's Voice", "Whenever an Aura you control enters, if you cast it, you "
     "may search your library for an Aura card with mana value less than or equal to that "
     "Aura, put that card onto the battlefield attached to Light-Paws, then shuffle.",
     "Creature"),
])
def test_a_search_the_engine_does_not_play_stays_a_gap(name, text, type_line):
    tutor = read(name, text, type_line=type_line, cost="{X}{G}{W}{U}")
    assert tutor.filter is None and "cannot do" in tutor.reason


def test_a_basic_land_goes_on_top():
    tutor = read("Loam Larva", "When this creature enters, you may search your library for a "
                 "basic land card, reveal it, then shuffle and put that card on top.")
    assert (tutor.zone, tutor.filter) == ("top", {"types": ["land"], "basic": True})


# --- the engine -------------------------------------------------------------------------

FOREST = Card("Forest", 0, 0, 0, LAND, subtypes=frozenset({"forest"}), basic=True,
              types=frozenset({"land"}))
VERGE = Card("Thornwood Falls", 0, 0, 0, LAND, subtypes=frozenset({"forest"}),
             types=frozenset({"land"}))
SWORD = Card("Sword", 3, 0, 3, ARTIFACT, cost=parse("{3}"), types=frozenset({"artifact"}),
             printed_subtypes=frozenset({"equipment"}))
GOLEM = Card("Golem", 4, 0, 4, CREATURE, cost=parse("{4}"), types=frozenset({"creature"}))
ELF = Card("Elf", 2, 0, 1, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}),
           creature_types=frozenset({"elf"}))
LEGEND = Card("Legend", 2, 0, 1, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}),
              legendary=True)
GOBLIN = Card("Goblin", 2, 0, 1, CREATURE, cost=parse("{1}{R}"), types=frozenset({"creature"}),
              creature_types=frozenset({"goblin"}))
BOLT = Card("Bolt", 1, 0, 1, SORCERY, cost=parse("{R}"), types=frozenset({"instant"}))
GODO = build.card("Godo", 5, 0, 5, CREATURE, cost=parse("{5}"), types=frozenset({"creature"}),
                  tutor=TutorSpec(to_hand=False, to_battlefield=True,
                                  types=frozenset({"equipment"})))
BELLOWER = build.card("Woodland Bellower", 6, 0, 6, CREATURE, cost=parse("{4}{G}{G}"),
                      types=frozenset({"creature"}),
                      tutor=TutorSpec(to_hand=False, to_battlefield=True, kind="creature",
                                      color="G", max_mv=3, nonlegendary=True))
MOGG = build.card("Moggcatcher", 4, 0, 4, CREATURE, cost=parse("{2}{R}{R}"),
                  types=frozenset({"creature"}),
                  tutor=TutorSpec(to_hand=False, to_battlefield=True, types=frozenset({"goblin"}),
                                  activate=True, cost=parse("{3}")))
BRIDGE = build.card("Planar Bridge", 6, 0, 6, ARTIFACT, cost=parse("{6}"),
                    types=frozenset({"artifact"}),
                    tutor=TutorSpec(to_hand=False, to_battlefield=True, permanent=True,
                                    activate=True, cost=parse("{8}")))


def game_with(library, *, lands=(), creatures=(), others=(), hand=()):
    game = Game(random.Random(1), deck=DeckDefinition("v34", None, (FOREST,)))
    game.library, game.lands = list(library), list(lands)
    game.creatures, game.other_permanents, game.hand = list(creatures), list(others), list(hand)
    tap(game, lands=0)
    game.turn = 3
    return game


def test_godo_puts_the_equipment_onto_the_battlefield():
    game = game_with([GOLEM, SWORD], lands=[FOREST] * 5, hand=[GODO])
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert SWORD in game.battlefield and GOLEM in game.library


def test_woodland_bellower_leaves_the_legend():
    game = game_with([LEGEND, ELF], lands=[FOREST] * 6, hand=[BELLOWER])
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert ELF in game.creatures and LEGEND in game.library


def test_neoform_finds_exactly_one_more_or_nothing():
    neoform = TutorSpec(to_hand=False, to_battlefield=True, kind="creature",
                        max_mv_sacrificed=1, exact_mv=True)
    game = game_with([ELF, GOLEM])
    game.last_sacrificed = ELF  # mana value 2: a 3 is wanted, and there is none
    actions.run_tutor(game, neoform, None)
    assert game.library == [ELF, GOLEM] and not game.creatures
    game.last_sacrificed = Card("Bear", 3, 0, 3, CREATURE)
    actions.run_tutor(game, neoform, None)
    assert GOLEM in game.creatures


def test_moggcatcher_waits_a_turn_then_finds_a_goblin_once():
    game = game_with([GOLEM, GOBLIN], lands=[FOREST] * 7, creatures=[MOGG])
    game.mark_arrived(MOGG)
    actions.OpenMainPhase().run(game, None)
    assert not game.can_activate(MOGG, game.pool), "not the turn it arrived"
    game.arrived = []
    assert actions.ActivateSearch(zone=actions.CREATURES, index=0) in actions.legal_actions(game)
    actions.ActivateSearch(zone=actions.CREATURES, index=0).run(game, None)
    assert GOBLIN in game.creatures and GOLEM in game.library
    assert game.pool.total == 4
    assert not game.can_activate(MOGG, game.pool), "once a turn"


def test_planar_bridge_finds_a_permanent_and_no_instant():
    game = game_with([BOLT, GOLEM], lands=[FOREST] * 8, others=[BRIDGE])
    actions.OpenMainPhase().run(game, None)
    actions.ActivateSearch(zone=actions.OTHER, index=0).run(game, None)
    assert GOLEM in game.creatures and game.library == [BOLT]


def test_the_agent_activates_it_with_mana_nothing_else_wants():
    game = game_with([GOBLIN], lands=[FOREST] * 3, creatures=[MOGG])
    actions.OpenMainPhase().run(game, None)
    assert agent._try_activation(game, game.pool)
    assert GOBLIN in game.creatures


def test_nothing_to_find_is_not_worth_activating():
    game = game_with([GOLEM], lands=[FOREST] * 3, creatures=[MOGG])
    actions.OpenMainPhase().run(game, None)
    assert not agent._try_activation(game, game.pool)


def test_an_embermouth_sentinel_puts_a_basic_on_top():
    top = TutorSpec(to_hand=False, to_top=True, types=frozenset({"land"}), basic=True)
    game = game_with([VERGE, FOREST, GOLEM])
    actions.run_tutor(game, top, None)
    assert game.library[0] is FOREST


# --- blocks and the card page -------------------------------------------------------------


@pytest.mark.parametrize("card", [MOGG, BRIDGE, BELLOWER])
def test_the_tutor_comes_back_the_same(card):
    again = serial.load(Card, json.loads(json.dumps(serial.dump(card))))
    assert again.play.tutor == card.play.tutor


def test_the_card_page_says_what_it_costs():
    assert Reading(None, MOGG).searches == (
        "{3} and {T}, once a turn: 1 × Goblin onto the battlefield")
    assert Reading(None, BRIDGE).searches == (
        "{8} and {T}, once a turn: 1 card, any permanent onto the battlefield")
    assert Reading(None, BELLOWER).searches == (
        "1 × Creature with mana value 3 or less (G), not legendary onto the battlefield")
