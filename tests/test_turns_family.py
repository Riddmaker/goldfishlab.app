"""Card model phase 4, PR 15: triggers, turns and combat as blocks.

What these tests hold:

1. **The family round-trips**: the eleven fields become blocks and come
   back exactly; a card made with fields equals one made with blocks.
2. **The view holds what the engine asks.**
3. **The engine reads blocks only**: `game.py`, `agent.py`, `actions.py` and
   `analysis.py` read the family through ``card.turn``.
4. **The engine card is face, hints and abilities**: every field `Card`
   keeps is printed or a play hint (`simulation.blocks`).

The whole catalogue's round trip is `engine_coverage --diff` against the
latest frozen reading; in CI it is the coverage snapshot of the 3,070 cards.
"""

import ast
import dataclasses
from pathlib import Path

import pytest

from simulation import serial
from simulation.blocks import FACE_FIELDS, HINT_FIELDS
from simulation.cards import (
    CREATURE,
    INIT_FIELDS,
    MANA_FIELDS,
    PLAY_FIELDS,
    TURN_FIELDS,
    Card,
    CombatTrigger,
    EndStepSpec,
    Growth,
    Trigger,
    UpkeepSpec,
)
from simulation.cards import fields as card_fields
from simulation.manacost import parse
from simulation.rules import mana, play, turns

#: Every field of the family away from its default.
FAMILY = {
    "upkeep": UpkeepSpec(draw=2, life=1, life_per_mv=True),
    "end_step": EndStepSpec(max_hand=5, life_floor=20),
    "skips_draw_step": True,
    "treasures": 2,
    "treasure_mana": "R",
    "triggers": (
        Trigger("landfall", filter="per_land", treasures=1, treasure_mana="G", mana=1,
                mana_color="B", draw=1, life=1, counter=1),
        Trigger("cast"),
        Trigger("cast"),
    ),
    "growth": Growth(enters=2, enters_x=True, grows=("creature",), adapt=3,
                     adapt_cost=parse("{2}{G}"), bonus=1, puts=1, draws_on_greatest=True,
                     look=3),
    "combat": (
        CombatTrigger("attacks", who="equipped", types=frozenset({"dragon"}), treasures=1,
                      treasure_mana="R", mana="RR", search=True, untapped=True),
        CombatTrigger("damage"),
    ),
    "equip": parse("{3}"),
    "germ": True,
    "treasure_bonus": 1,
}


def test_the_family_is_every_field_card_takes():
    assert tuple(turns.FIELDS) == TURN_FIELDS
    assert set(FAMILY) == set(turns.FIELDS)
    assert all(FAMILY[name] != default for name, default in turns.FIELDS.items())
    assert set(MANA_FIELDS) | set(PLAY_FIELDS) | set(TURN_FIELDS) == set(INIT_FIELDS)
    assert not turns.FORMS & (mana.FORMS | play.FORMS)


def test_every_field_comes_back_exactly():
    assert turns.decode(turns.encode(FAMILY)) == FAMILY
    assert turns.decode(turns.encode({})) == turns.FIELDS
    assert turns.encode({}) == ()


@pytest.mark.parametrize("name", list(FAMILY))
def test_each_field_comes_back_alone(name):
    assert turns.decode(turns.encode({name: FAMILY[name]})) == {**turns.FIELDS,
                                                                 name: FAMILY[name]}


def test_treasure_mana_alone_is_kept():
    assert turns.decode(turns.encode({"treasure_mana": "G"}))["treasure_mana"] == "G"


def test_a_card_made_with_fields_is_a_card_made_with_blocks():
    card = Card("Everything", 3, 1, 2, CREATURE, ritual_gain=2, draw_on_cast=1, **FAMILY)
    again = Card("Everything", 3, 1, 2, CREATURE,
                 abilities=mana.encode({"ritual_gain": 2}) + play.encode({"draw_on_cast": 1})
                 + turns.encode(FAMILY))
    assert card == again and hash(card) == hash(again)
    assert {name: card_fields(card)[name] for name in TURN_FIELDS} == FAMILY
    changed = dataclasses.replace(card, germ=False)
    assert changed == Card("Everything", 3, 1, 2, CREATURE, ritual_gain=2, draw_on_cast=1,
                           **{**FAMILY, "germ": False})


def test_a_stored_card_keeps_its_blocks():
    card = Card("Everything", 3, 1, 2, CREATURE, **FAMILY)
    dumped = serial.dump(card)
    assert "growth" not in dumped and dumped["abilities"]
    assert serial.load(Card, dumped) == card


def test_the_view_holds_what_the_engine_asks():
    view = Card("Everything", 3, 1, 2, CREATURE, **FAMILY).turn
    assert {name: getattr(view, name) for name in TURN_FIELDS} == FAMILY
    assert Card("Bear", 2, 0, 2, CREATURE).turn is turns.NO_TURN


def test_the_engine_card_is_face_hints_and_abilities():
    kept = {item.name for item in dataclasses.fields(Card)} - {"abilities"}
    assert kept == set(FACE_FIELDS) | set(HINT_FIELDS)


# --- the engine reads blocks only ---------------------------------------------------------

ENGINE = ("game.py", "agent.py", "actions.py", "analysis.py")

#: What the family's names are read off besides a card's view: the game,
#: a mana pool, a trigger, a tutor (its own ``upkeep``).
NOT_CARDS = {"self", "game", "pool", "test", "trigger", "tutor"}


@pytest.mark.parametrize("module", ENGINE)
def test_the_engine_reads_the_family_through_its_view(module):
    path = Path(__file__).resolve().parent.parent / "simulation" / module
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in turns.FIELDS:
            owner = node.value
            assert ((isinstance(owner, ast.Attribute) and owner.attr in NOT_CARDS | {"turn"})
                    or (isinstance(owner, ast.Name) and owner.id in NOT_CARDS)), (
                f"{module}:{node.lineno} reads .{node.attr} off a card")
