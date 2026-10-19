"""Triggers, turns and combat as blocks: encoder, decoder, the engine's view (phase 4, PR 15).

Eleven fields of the old engine card said what a card does while turns
pass: its triggered abilities, its upkeep and end step, a skipped draw
step, its +1/+1 counters, its triggers in combat, an Equipment's equip
cost, a Germ, the Treasures it makes as it resolves or enters and the
mana they make, and a Treasure bonus. PR 15 turns them into blocks the way
PR 13 and 14 turned the mana family and lands, costs and spells; with it
every rule of the old card is a block, and the card is printed face, play
hints and abilities.

The blocks are the ones phase 0 described these structures with
(:data:`simulation.blocks.FORMS`, proven lossless on the whole catalogue
then): one encoder and one decoder per structure, used here as they are.
Treasures, which phase 0 kept in a spell's resolve block, get a block of
their own (``treasures``), since the resolve block belongs to PR 14.

Three functions, as in :mod:`simulation.rules.mana`:

* `encode` turns the eleven fields into blocks;
* `decode` turns the blocks back into exactly those fields, for the
  application until phase 5;
* `compile_view` turns a card's blocks into a `TurnView` once: what the
  engine asks while it plays.

A trigger, an upkeep, an end step, a growth and a combat trigger reach the
engine in today's shapes (`Trigger`, `UpkeepSpec`, `EndStepSpec`, `Growth`,
`CombatTrigger`), as a land search does since PR 14: the engine's turn
code takes them so, and they move to atoms when phase 5 needs it.
"""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from simulation.blocks import FORMS as STRUCTURES
from simulation.blocks import RESOLVES, Ability, Effect
from simulation.cards import CombatTrigger, EndStepSpec, Growth, Trigger, UpkeepSpec
from simulation.manacost import ManaCost

# --- the eleven fields ------------------------------------------------------------------

#: The fields of the family, in the order the card declares them, with
#: their defaults.
FIELDS: dict[str, Any] = {
    "upkeep": None,
    "end_step": None,
    "skips_draw_step": False,
    "treasures": 0,
    "treasure_mana": "",
    "triggers": (),
    "growth": None,
    "combat": (),
    "equip": None,
    "germ": False,
    "treasure_bonus": 0,
}

#: The phase 0 structures of the family: the key of each in
#: `simulation.blocks.FORMS`, and the ``form`` its blocks carry.
_STRUCTURES = {"upkeep": "upkeep", "end_step": "end_step", "skips_draw": "skips_draw",
               "triggers": "trigger", "combat": "combat", "growth": "growth",
               "equip": "equip", "germ": "germ", "treasure_bonus": "treasure_bonus"}

#: The ``form`` of every block of the family.
FORMS = frozenset(_STRUCTURES.values()) | {"treasures"}

_BY_FORM = {form: key for key, form in _STRUCTURES.items()}


# --- encoding and decoding ----------------------------------------------------------------------


def encode(values: dict) -> tuple[Ability, ...]:
    """The family's fields as blocks; a field at its default makes none."""
    card = SimpleNamespace(**{**FIELDS, **values})
    found: list[Ability] = []
    for key in _STRUCTURES:
        found.extend(STRUCTURES[key][0](card))
    if card.treasures or card.treasure_mana:
        # The mana of Treasures a card does not make is a value nothing
        # reads; it is kept so that the card comes back the same.
        found.append(Ability(RESOLVES, "treasures", effects=(Effect("create_token", (
            ("mana", card.treasure_mana), ("n", card.treasures), ("token", "treasure"))),)))
    return tuple(found)


def decode(abilities) -> dict:
    """The family's fields from blocks, every one: defaults where none says."""
    out = dict(FIELDS)
    grouped: dict[str, list[Ability]] = {}
    for ability in abilities:
        if ability.form == "treasures":
            effect = ability.effects[0]
            out.update(treasures=effect.arg("n"), treasure_mana=effect.arg("mana"))
        elif ability.form in _BY_FORM:
            grouped.setdefault(_BY_FORM[ability.form], []).append(ability)
    for key, found in grouped.items():
        out.update(STRUCTURES[key][1](found))
    return out


# --- what the engine reads ----------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class TurnView:
    """A card's triggers, turns and combat, compiled once: everything the
    engine asks. The names are the old fields'."""

    upkeep: UpkeepSpec | None = None
    end_step: EndStepSpec | None = None
    skips_draw_step: bool = False
    treasures: int = 0
    treasure_mana: str = ""
    triggers: tuple[Trigger, ...] = ()
    growth: Growth | None = None
    combat: tuple[CombatTrigger, ...] = ()
    equip: ManaCost | None = None
    germ: bool = False
    treasure_bonus: int = 0


NO_TURN = TurnView()


def compile_view(abilities) -> TurnView:
    """The engine's view of a card's blocks of triggers, turns and combat."""
    if not any(each.form in FORMS for each in abilities):
        return NO_TURN
    return TurnView(**decode(abilities))
