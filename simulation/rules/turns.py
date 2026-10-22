"""Triggers, turns and combat as blocks: encoder and what the engine reads (phase 4, PR 15).

Eleven fields of the old engine card said what a card does while turns
pass: its triggered abilities, its upkeep and end step, a skipped draw
step, its +1/+1 counters, its triggers in combat, an Equipment's equip
cost, a Germ, the Treasures it makes as it resolves or enters and the
mana they make, and a Treasure bonus. PR 15 turned them into blocks the way
PR 13 and 14 turned the mana family and lands, costs and spells.

Two functions, as in :mod:`simulation.rules.mana`:

* `encode` turns the eleven fields into blocks (`simulation.build`), one
  encoder per structure;
* `compile_view` turns a card's blocks into a `TurnView` once: what the
  engine asks while it plays.

A trigger, an upkeep, an end step, a growth and a combat trigger reach the
engine in the shapes its turn code takes (`Trigger`, `UpkeepSpec`,
`EndStepSpec`, `Growth`, `CombatTrigger`), read off their blocks' atoms.
"""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from simulation.blocks import (
    ACTIVATED,
    RESOLVES,
    STATIC,
    TRIGGERED,
    Ability,
    Cost,
    Effect,
    Event,
    changed_args,
)
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

#: The structures of the family, and the ``form`` their blocks carry.
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
        found.extend(_ENCODERS[key](card))
    if card.treasures or card.treasure_mana:
        # The mana of Treasures a card does not make is a value nothing
        # reads; it is kept so that the card comes back the same.
        found.append(Ability(RESOLVES, "treasures", effects=(Effect("create_token", (
            ("mana", card.treasure_mana), ("n", card.treasures), ("token", "treasure"))),)))
    return tuple(found)


# --- one encoder and one reader per structure -----------------------------------------


def _enc_upkeep(card):
    value = card.upkeep
    if value is None:
        return
    effects = [Effect("draw", (("n", value.draw),))]
    if value.life or value.life_per_mv:
        effects.append(Effect("lose_life", tuple(sorted(
            [("n", value.life)] + ([("per", "drawn_mv")] if value.life_per_mv else [])))))
    yield Ability(TRIGGERED, "upkeep", event=Event("upkeep"), effects=tuple(effects))


def _upkeep_of(abilities):
    effects = {effect.verb: effect for effect in abilities[0].effects}
    life = effects.get("lose_life")
    return {"upkeep": UpkeepSpec(draw=effects["draw"].arg("n"),
                                 life=life.arg("n") if life else 0,
                                 life_per_mv=bool(life and life.arg("per")))}


def _enc_end_step(card):
    if card.end_step is not None:
        yield Ability(TRIGGERED, "end_step", event=Event("end_step"),
                      effects=(Effect("pay_life_draw",
                                      changed_args(card.end_step) + (("set", True),)),))


def _end_step_of(abilities):
    args = dict(abilities[0].effects[0].args)
    args.pop("set")
    return {"end_step": EndStepSpec(**args)}


def _enc_skips_draw(card):
    if card.skips_draw_step:
        yield Ability(STATIC, "skips_draw", effects=(Effect("skip_draw_step"),))


def _skips_draw_of(abilities):
    return {"skips_draw_step": True}


def _trigger_effects(treasures, treasure_mana, mana, mana_color, draw, life, counter,
                     connive):
    effects = []
    if treasures:
        effects.append(Effect("create_token", (("mana", treasure_mana), ("n", treasures),
                                               ("token", "treasure"))))
    if mana:
        effects.append(Effect("add_mana", (("color", mana_color), ("n", mana))))
    if draw:
        effects.append(Effect("draw", (("n", draw),)))
    if life:
        effects.append(Effect("lose_life", (("n", life),)))
    if counter:
        effects.append(Effect("put_counter", (("n", counter), ("on", "self"))))
    if connive:
        effects.append(Effect("connive", (("n", connive),)))
    return tuple(effects)


def _enc_triggers(card):
    for value in card.triggers:
        effects = _trigger_effects(value.treasures, value.treasure_mana, value.mana,
                                   value.mana_color, value.draw, value.life, value.counter,
                                   value.connive)
        if value.draw_by is not None:
            # v34: Champion of Dusk draws a card for each Vampire.
            effects += (Effect("draw", (("by", value.draw_by),)
                               + ((("lose", True),) if value.lose_drawn else ())),)
        # A trigger that does nothing the engine plays still holds its place.
        yield Ability(TRIGGERED, "trigger", event=Event(value.event, filter=value.filter),
                      effects=effects or (Effect("nothing", changed_args(value, skip=("event",
                                                                               "filter"))),))


def _triggers_of(abilities):
    found = []
    for ability in abilities:
        kwargs: dict[str, Any] = {"event": ability.event.name, "filter": ability.event.filter}
        for effect in ability.effects:
            if effect.verb == "create_token":
                kwargs.update(treasures=effect.arg("n"), treasure_mana=effect.arg("mana"))
            elif effect.verb == "add_mana":
                kwargs.update(mana=effect.arg("n"), mana_color=effect.arg("color"))
            elif effect.verb == "draw" and effect.arg("by") is not None:
                kwargs.update(draw_by=effect.arg("by"), lose_drawn=effect.arg("lose", False))
            elif effect.verb == "draw":
                kwargs["draw"] = effect.arg("n")
            elif effect.verb == "lose_life":
                kwargs["life"] = effect.arg("n")
            elif effect.verb == "put_counter":
                kwargs["counter"] = effect.arg("n")
            elif effect.verb == "connive":
                kwargs["connive"] = effect.arg("n")
            else:
                kwargs.update(dict(effect.args))
        found.append(Trigger(**kwargs))
    return {"triggers": tuple(found)}


def _enc_combat(card):
    for value in card.combat:
        effects = []
        if value.treasures:
            effects.append(Effect("create_token", (("mana", value.treasure_mana),
                                                   ("n", value.treasures),
                                                   ("token", "treasure"))))
        if value.mana:
            effects.append(Effect("add_mana", (("mana", value.mana), ("until", "end_of_turn"))))
        if value.search or value.untapped:
            effects.append(Effect("search_land", (("search", value.search),
                                                  ("untapped", value.untapped))))
        if value.to_hand:
            # Engine v37: Captain Kirk's basic land to hand.
            effects.append(Effect("search_land", (("to_hand", True),)))
        if value.connive:
            effects.append(Effect("connive", (("n", value.connive),)))
        yield Ability(TRIGGERED, "combat",
                      event=Event(value.event, who=value.who, types=value.types),
                      effects=tuple(effects))


def _combat_of(abilities):
    found = []
    for ability in abilities:
        kwargs: dict[str, Any] = {"event": ability.event.name, "who": ability.event.who,
                                  "types": ability.event.types}
        for effect in ability.effects:
            if effect.verb == "create_token":
                kwargs.update(treasures=effect.arg("n"), treasure_mana=effect.arg("mana"))
            elif effect.verb == "add_mana":
                kwargs["mana"] = effect.arg("mana")
            elif effect.verb == "connive":
                kwargs["connive"] = effect.arg("n")
            elif effect.arg("to_hand"):
                kwargs["to_hand"] = True
            else:
                kwargs.update(search=effect.arg("search"), untapped=effect.arg("untapped"))
        found.append(CombatTrigger(**kwargs))
    return {"combat": tuple(found)}


def _enc_growth(card):
    if card.growth is not None:
        value = card.growth
        effects = [Effect("counters", changed_args(value, skip=("adapt", "adapt_cost"))
                          + (("set", True),))]
        if value.adapt or value.adapt_cost is not None:
            effects.append(Effect("adapt", (("n", value.adapt),)))
        costs = (Cost("mana", mana=value.adapt_cost),) if value.adapt_cost else ()
        yield Ability(STATIC, "growth", costs=costs, effects=tuple(effects))


def _growth_of(abilities):
    ability = abilities[0]
    args = dict(ability.effects[0].args)
    args.pop("set")
    adapt = next((effect for effect in ability.effects if effect.verb == "adapt"), None)
    mana = next((cost for cost in ability.costs if cost.atom == "mana"), None)
    return {"growth": Growth(**args, adapt=adapt.arg("n") if adapt else 0,
                             adapt_cost=mana.mana if mana else None)}


def _enc_equip(card):
    if card.equip is not None:
        yield Ability(ACTIVATED, "equip", costs=(Cost("mana", mana=card.equip),),
                      effects=(Effect("attach"),))


def _equip_of(abilities):
    return {"equip": abilities[0].costs[0].mana}


def _enc_germ(card):
    if card.germ:
        yield Ability(TRIGGERED, "germ", event=Event("enters_self"),
                      effects=(Effect("create_token", (("attach", True), ("n", 1),
                                                       ("token", "germ"))),))


def _germ_of(abilities):
    return {"germ": True}


def _enc_treasure_bonus(card):
    if card.treasure_bonus:
        yield Ability(STATIC, "treasure_bonus",
                      effects=(Effect("more_mana", (("from", "treasure"),
                                                    ("n", card.treasure_bonus))),))


def _treasure_bonus_of(abilities):
    return {"treasure_bonus": abilities[0].effects[0].arg("n")}


_ENCODERS = {"upkeep": _enc_upkeep, "end_step": _enc_end_step, "skips_draw": _enc_skips_draw,
             "triggers": _enc_triggers, "combat": _enc_combat, "growth": _enc_growth,
             "equip": _enc_equip, "germ": _enc_germ, "treasure_bonus": _enc_treasure_bonus}
_READERS = {"upkeep": _upkeep_of, "end_step": _end_step_of, "skips_draw": _skips_draw_of,
            "triggers": _triggers_of, "combat": _combat_of, "growth": _growth_of,
            "equip": _equip_of, "germ": _germ_of, "treasure_bonus": _treasure_bonus_of}


def _read(abilities) -> dict:
    """What the blocks say, by the field each came from: defaults where none
    says."""
    out = dict(FIELDS)
    grouped: dict[str, list[Ability]] = {}
    for ability in abilities:
        if ability.form == "treasures":
            effect = ability.effects[0]
            out.update(treasures=effect.arg("n"), treasure_mana=effect.arg("mana"))
        elif ability.form in _BY_FORM:
            grouped.setdefault(_BY_FORM[ability.form], []).append(ability)
    for key, found in grouped.items():
        out.update(_READERS[key](found))
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
    return TurnView(**_read(abilities))
