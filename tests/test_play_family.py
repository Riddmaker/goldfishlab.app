"""Card model phase 4, PR 14: lands, costs and spells as blocks.

What these tests hold:

1. **The family round-trips**: the thirteen fields become blocks and come
   back exactly; a card made with fields equals one made with blocks, and
   a card changed with `dataclasses.replace` equals one made so.
2. **The view holds what the engine asks**: a land's condition compiled,
   its search, its tutor, the ways its additional cost is paid, what it
   does as it resolves.
3. **The engine reads blocks only**: `game.py`, `mana.py`, `agent.py`,
   `actions.py` and `analysis.py` read the family through ``card.play``.

The whole catalogue's round trip is `engine_coverage --diff` against the
latest frozen reading; in CI it is the coverage snapshot of the 3,070 cards.
"""

import ast
import dataclasses
from pathlib import Path

import pytest

from simulation import serial
from simulation.cards import (
    INIT_FIELDS,
    LAND,
    MANA_FIELDS,
    PLAY_FIELDS,
    SORCERY,
    AdditionalCost,
    Card,
    LandSearch,
    TappedUnless,
    TutorSpec,
)
from simulation.manacost import parse
from simulation.rules import mana, play
from simulation.rules.condition import Condition
from tests.engine_builders import game_with

#: Every field of the family away from its default.
FAMILY = {
    "enters_tapped": True,
    "draw_on_cast": 2,
    "life_on_cast": 1,
    "tutor": TutorSpec(to_hand=False, count=3, life=2, kind="creature", max_mv=3,
                       types=frozenset({"artifact"}), upkeep="opponent_creatures"),
    "land_search": LandSearch(battlefield=2, hand=1, tapped=False, basic=False,
                              types=frozenset({"forest"}), life=1, when="activate",
                              sacrifice=True, cost=parse("{2}"), taps=True,
                              condition="opponent_more_lands", sacrifices_land=True,
                              land_cost_types=frozenset({"plains"}),
                              sacrifice_other=frozenset({"creature"}), discard=1),
    "tapped_unless": TappedUnless("pay_life", count=2),
    "landers": 2,
    "discard_cost": 1,
    "discard_on_cast": 2,
    "put_back_on_cast": 2,
    "draws_x": True,
    "additional_costs": (
        AdditionalCost(sacrifice=frozenset({"creature"}), sacrifice_filter="legendary",
                       sacrifice_count=2),
        AdditionalCost(life=3, life_x=True),
        AdditionalCost(discard=1, mana=parse("{1}")),
        AdditionalCost(exile_from_graveyard="creature"),
        AdditionalCost(),
    ),
    "exiled_on_cast": True,
}


def test_the_family_is_every_field_card_takes():
    assert tuple(play.FIELDS) == PLAY_FIELDS
    assert set(FAMILY) == set(play.FIELDS)
    assert all(FAMILY[name] != default for name, default in play.FIELDS.items())
    assert not set(MANA_FIELDS) & set(PLAY_FIELDS) and set(PLAY_FIELDS) <= set(INIT_FIELDS)
    assert not mana.FORMS & play.FORMS


def test_every_field_comes_back_exactly():
    assert play.decode(play.encode(FAMILY)) == FAMILY
    assert play.decode(play.encode({})) == play.FIELDS
    assert play.encode({}) == ()


@pytest.mark.parametrize("name", list(FAMILY))
def test_each_field_comes_back_alone(name):
    assert play.decode(play.encode({name: FAMILY[name]})) == {**play.FIELDS,
                                                               name: FAMILY[name]}


def test_a_card_made_with_fields_is_a_card_made_with_blocks():
    card = Card("Everything", 3, 1, 2, SORCERY, ritual_gain=2, **FAMILY)
    again = Card("Everything", 3, 1, 2, SORCERY,
                 abilities=mana.encode({"ritual_gain": 2}) + play.encode(FAMILY))
    assert card == again and hash(card) == hash(again)
    assert {name: getattr(card, name) for name in PLAY_FIELDS} == FAMILY
    assert card.ritual_gain == 2


def test_a_changed_card_equals_one_made_so():
    card = Card("Everything", 3, 1, 2, SORCERY, ritual_gain=2, **FAMILY)
    changed = dataclasses.replace(card, ritual_gain=5)
    assert changed == Card("Everything", 3, 1, 2, SORCERY, ritual_gain=5, **FAMILY)
    assert changed.land_search == FAMILY["land_search"]
    fewer = dataclasses.replace(card, landers=0)
    assert fewer.landers == 0 and fewer.draws_x
    assert dataclasses.replace(card, priority=3).abilities == card.abilities


def test_enters_tapped_is_still_the_eighth_argument():
    land = Card("Tapland", 0, 0, 0, LAND, frozenset(), "", True)
    assert land.enters_tapped and land.play.enters_tapped


def test_a_stored_card_keeps_the_field_form():
    card = Card("Everything", 3, 1, 2, SORCERY, **FAMILY)
    dumped = serial.dump(card)
    assert "abilities" not in dumped and dumped["land_search"]["_type"] == "LandSearch"
    assert serial.load(Card, dumped) == card


def test_the_view_holds_what_the_engine_asks():
    view = Card("Everything", 3, 1, 2, SORCERY, **FAMILY).play
    assert view.enters_tapped and view.untapped_if == Condition("pay_life", count=2)
    assert view.search == FAMILY["land_search"] and view.tutor == FAMILY["tutor"]
    assert view.ways == FAMILY["additional_costs"] and view.discard_cost == 1
    assert (view.draw, view.draws_x, view.discard, view.put_back, view.life, view.landers) == (
        2, True, 2, 2, 1, 2)
    assert view.exiled
    assert Card("Bear", 2, 0, 2, "creature").play is play.NO_PLAY


def test_a_land_asks_its_compiled_condition():
    swamp = Card("Swamp", 0, 0, 0, LAND, subtypes=frozenset({"swamp"}))
    check = Card("Check Land", 0, 0, 0, LAND, enters_tapped=True,
                 tapped_unless=TappedUnless("control_type", types=frozenset({"swamp"})))
    assert game_with(lands=[swamp]).untaps_on_entering(check)
    assert not game_with().untaps_on_entering(check)
    assert not game_with(lands=[swamp]).untaps_on_entering(swamp)


def test_a_shock_land_pays_its_life_from_the_view():
    shock = Card("Shock Land", 0, 0, 0, LAND, enters_tapped=True,
                 tapped_unless=TappedUnless("pay_life", count=2))
    game = game_with(hand=[shock])
    game.play_land(shock)
    assert game.life == 38 and game.tapped_lands == 0


# --- the engine reads blocks only ---------------------------------------------------------

ENGINE = ("game.py", "mana.py", "agent.py", "actions.py", "analysis.py")

#: The family's fields that are not also the name of something in the view.
FORBIDDEN = {"tapped_unless", "land_search", "additional_costs", "exiled_on_cast",
             "draw_on_cast", "life_on_cast", "discard_on_cast", "put_back_on_cast"}
#: Shared with the view (``enters_tapped``) or with the game (``landers``):
#: read only off ``.play``, or off the game.
SHARED = {"enters_tapped", "tutor", "discard_cost", "draws_x", "landers"}


@pytest.mark.parametrize("module", ENGINE)
def test_the_engine_reads_the_family_through_its_view(module):
    path = Path(__file__).resolve().parent.parent / "simulation" / module
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN, f"{module}:{node.lineno} reads .{node.attr}"
            if node.attr in SHARED:
                owner = node.value
                assert ((isinstance(owner, ast.Attribute) and owner.attr == "play")
                        or (isinstance(owner, ast.Name) and owner.id in ("self", "game"))), (
                    f"{module}:{node.lineno} reads .{node.attr} off a card")
        if isinstance(node, ast.Name):
            assert node.id != "TappedUnless", f"{module}:{node.lineno} names TappedUnless"
