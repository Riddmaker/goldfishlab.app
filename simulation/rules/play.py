"""Lands, costs and spells as blocks: encoder and what the engine reads (phase 4, PR 14).

Thirteen fields of the old engine card said how a card is played: whether a
land enters tapped and the condition under which it does not, the lands a
card searches for, its Landers, its tutor, the costs a spell asks besides
mana (a discard, the additional costs), whether it is exiled once cast, and
what it does as it resolves (draw, draw X, discard, put back, lose life).
PR 14 turns them into blocks the way PR 13 turned the mana family: the
`Condition` atom where the board is asked, `Cost` atoms with a `Selector`
where something is paid.

Two functions, as in :mod:`simulation.rules.mana`:

* `encode` turns the thirteen fields, as the adapter compiles them, into
  blocks (`simulation.build`).
* `compile_view` turns a card's blocks into a `PlayView` once: what the
  engine asks while it plays.

A land search, a tutor and one way of paying an additional cost reach the
engine in today's shapes (`LandSearch`, `TutorSpec`, `AdditionalCost`):
the engine's search and payment code takes Landers, combat searches and
altars in those shapes too, and they move to atoms only when phase 5 needs
it. Their costs and conditions are atoms in the blocks.

Each block names the field it came from (``form``), as in PR 13.
"""

from dataclasses import dataclass
from typing import Any

from simulation.blocks import (
    ACTIVATED,
    CAST,
    LAND_SEARCH_WHEN,
    REPLACEMENT,
    RESOLVES,
    STATIC,
    TRIGGERED,
    Ability,
    Cost,
    Effect,
    Event,
    changed_args,
)
from simulation.cards import AdditionalCost, LandSearch, TutorSpec
from simulation.rules.condition import Condition, check
from simulation.rules.selector import Selector, wanted, wanted_of

# --- the thirteen fields -------------------------------------------------------------------

#: The fields of the family, in the order the card declares them, with
#: their defaults.
FIELDS: dict[str, Any] = {
    "enters_tapped": False,
    "draw_on_cast": 0,
    "life_on_cast": 0,
    "tutor": None,
    "land_search": None,
    "tapped_unless": None,
    "landers": 0,
    "discard_cost": 0,
    "discard_on_cast": 0,
    "put_back_on_cast": 0,
    "draws_x": False,
    "additional_costs": (),
    "exiled_on_cast": False,
    "draw_by": None,
    "lose_drawn": False,
}

#: The ``form`` of every block of the family.
FORMS = frozenset({"enters_tapped", "land_search", "tutor", "cast_costs", "spell_resolves",
                   "exiled_on_cast"})

#: What a spell does as it resolves, in the order the engine plays it, as
#: ``(field, verb)``; drawing (with X) comes first and Landers last.
_RESOLVES = (("discard_on_cast", "discard"), ("put_back_on_cast", "put_back"),
             ("life_on_cast", "lose_life"))

#: The fields of a land search its costs and condition hold.
_SEARCH_COSTS = ("taps", "cost", "life", "sacrifice", "sacrifices_land", "land_cost_types",
                 "sacrifice_other", "discard", "when", "condition")

LAND = frozenset({"land"})


# --- encoding ---------------------------------------------------------------------------------


def _option(way: AdditionalCost) -> tuple[Cost, ...]:
    """One way of paying an additional cost as cost atoms."""
    atoms = []
    if way.sacrifice or way.sacrifice_filter or way.sacrifice_count != 1:
        atoms.append(Cost("sacrifice", amount=way.sacrifice_count,
                          selector=wanted(way.sacrifice, way.sacrifice_filter)))
    if way.life or way.life_x:
        atoms.append(Cost("life", amount=way.life, x=way.life_x))
    if way.discard:
        atoms.append(Cost("discard", amount=way.discard))
    if way.mana is not None:
        atoms.append(Cost("mana", mana=way.mana))
    if way.exile_from_graveyard:
        atoms.append(Cost("exile_from_graveyard", amount=1,
                          selector=Selector(types=frozenset({way.exile_from_graveyard}))))
    return tuple(atoms)


def _search_costs(search: LandSearch) -> tuple[Cost, ...]:
    costs = []
    if search.taps:
        costs.append(Cost("tap"))
    if search.cost is not None:
        costs.append(Cost("mana", mana=search.cost))
    if search.life:
        costs.append(Cost("life", amount=search.life))
    if search.sacrifice:
        costs.append(Cost("sacrifice_self"))
    if search.sacrifices_land:
        costs.append(Cost("sacrifice_land", amount=1,
                          selector=Selector(types=LAND, subtypes=search.land_cost_types)))
    if search.sacrifice_other:
        costs.append(Cost("sacrifice", amount=1, other=True,
                          selector=wanted(search.sacrifice_other, "")))
    if search.discard:
        costs.append(Cost("discard", amount=search.discard))
    return tuple(costs)


def _land_search(search: LandSearch) -> Ability:
    kind, event = LAND_SEARCH_WHEN.get(search.when, (TRIGGERED, search.when))
    return Ability(kind, "land_search", costs=_search_costs(search),
                   event=Event(event) if event else None,
                   conds=(Condition(search.condition),) if search.condition else (),
                   effects=(Effect("search_land", changed_args(search, skip=_SEARCH_COSTS)
                                   + (("when", search.when),)),))


def _tutor(tutor: TutorSpec) -> Ability:
    event = Event("upkeep", filter=tutor.upkeep) if tutor.upkeep else None
    costs = (Cost("life", amount=tutor.life),) if tutor.life else ()
    kind = TRIGGERED if event else RESOLVES
    if tutor.activate:
        # v34: Moggcatcher's "{3}, {T}: ".
        kind = ACTIVATED
        costs += (Cost("tap"),) + ((Cost("mana", mana=tutor.cost),) if tutor.cost else ())
    return Ability(kind, "tutor", costs=costs, event=event,
                   effects=(Effect("search", changed_args(
                       tutor, skip=("life", "upkeep", "activate", "cost"))),))


def encode(values: dict) -> tuple[Ability, ...]:
    """The family's fields as blocks; a field at its default makes none."""
    get = {**FIELDS, **values}.__getitem__
    found = []
    unless = get("tapped_unless")
    if get("enters_tapped") or unless is not None:
        # A condition on a land that does not enter tapped is a value
        # nothing reads; it is kept so that the card comes back the same.
        found.append(Ability(STATIC, "enters_tapped",
                             conds=(Condition.of(unless),) if unless is not None else (),
                             effects=(Effect("enters_tapped", () if get("enters_tapped")
                                             else (("off", True),)),)))
    if get("land_search") is not None:
        found.append(_land_search(get("land_search")))
    if get("tutor") is not None:
        found.append(_tutor(get("tutor")))
    if get("discard_cost") or get("additional_costs"):
        found.append(Ability(CAST, "cast_costs",
                             costs=(Cost("discard", amount=get("discard_cost")),)
                             if get("discard_cost") else (),
                             options=tuple(_option(each) for each in get("additional_costs"))))
    effects = []
    if get("draw_on_cast") or get("draws_x"):
        effects.append(Effect("draw", (("n", get("draw_on_cast")),)
                              + ((("x", True),) if get("draws_x") else ())))
    if get("draw_by") is not None:
        # v34: Rishkar's Expertise draws by the greatest power.
        effects.append(Effect("draw", (("by", get("draw_by")),)
                              + ((("lose", True),) if get("lose_drawn") else ())))
    effects += [Effect(verb, (("n", get(name)),)) for name, verb in _RESOLVES if get(name)]
    if get("landers"):
        effects.append(Effect("create_token", (("n", get("landers")), ("token", "lander"))))
    if effects:
        found.append(Ability(RESOLVES, "spell_resolves", effects=tuple(effects)))
    if get("exiled_on_cast"):
        found.append(Ability(REPLACEMENT, "exiled_on_cast", effects=(Effect("exile_instead"),)))
    return tuple(found)


# --- what a block holds, for the view ---------------------------------------------------------


def _way(atoms: tuple[Cost, ...]) -> AdditionalCost:
    """`_option` backwards."""
    found = {atom.atom: atom for atom in atoms}
    out: dict[str, Any] = {}
    sacrifice = found.get("sacrifice")
    if sacrifice is not None:
        types, filter_ = wanted_of(sacrifice.selector)
        out.update(sacrifice=types, sacrifice_filter=filter_, sacrifice_count=sacrifice.amount)
    if "life" in found:
        out.update(life=found["life"].amount, life_x=found["life"].x)
    if "discard" in found:
        out["discard"] = found["discard"].amount
    if "mana" in found:
        out["mana"] = found["mana"].mana
    if "exile_from_graveyard" in found:
        out["exile_from_graveyard"] = next(iter(found["exile_from_graveyard"].selector.types))
    return AdditionalCost(**out)


def _search_of(ability: Ability) -> LandSearch:
    """`_land_search` backwards."""
    atoms = {cost.atom: cost for cost in ability.costs}
    land, other = atoms.get("sacrifice_land"), atoms.get("sacrifice")
    return LandSearch(
        **dict(ability.effects[0].args),
        taps="tap" in atoms,
        cost=atoms["mana"].mana if "mana" in atoms else None,
        life=atoms["life"].amount if "life" in atoms else 0,
        sacrifice="sacrifice_self" in atoms,
        sacrifices_land=land is not None,
        land_cost_types=land.selector.subtypes if land else frozenset(),
        sacrifice_other=wanted_of(other.selector)[0] if other else frozenset(),
        discard=atoms["discard"].amount if "discard" in atoms else 0,
        condition=ability.conds[0].kind if ability.conds else "",
    )


def _tutor_of(ability: Ability) -> TutorSpec:
    atoms = {cost.atom: cost for cost in ability.costs}
    return TutorSpec(**dict(ability.effects[0].args),
                     life=atoms["life"].amount if "life" in atoms else 0,
                     upkeep=ability.event.filter if ability.event else "",
                     activate=ability.kind == ACTIVATED,
                     cost=atoms["mana"].mana if "mana" in atoms else None)


def _read(abilities) -> dict:
    """What the blocks say, by the field each came from: defaults where none
    says."""
    out = dict(FIELDS)
    by_verb = {verb: name for name, verb in _RESOLVES}
    for ability in abilities:
        form = ability.form
        if form == "enters_tapped":
            out["enters_tapped"] = not ability.effects[0].arg("off", False)
            out["tapped_unless"] = ability.conds[0] if ability.conds else None
        elif form == "land_search":
            out["land_search"] = _search_of(ability)
        elif form == "tutor":
            out["tutor"] = _tutor_of(ability)
        elif form == "cast_costs":
            out["discard_cost"] = ability.costs[0].amount if ability.costs else 0
            out["additional_costs"] = tuple(_way(option) for option in ability.options)
        elif form == "spell_resolves":
            for effect in ability.effects:
                if effect.verb == "draw" and effect.arg("by") is not None:
                    out["draw_by"] = effect.arg("by")
                    out["lose_drawn"] = effect.arg("lose", False)
                elif effect.verb == "draw":
                    out["draw_on_cast"] = effect.arg("n")
                    out["draws_x"] = effect.arg("x", False)
                elif effect.verb == "create_token":
                    out["landers"] = effect.arg("n")
                else:
                    out[by_verb[effect.verb]] = effect.arg("n")
        elif form == "exiled_on_cast":
            out["exiled_on_cast"] = True
    return out


# --- what the engine reads ----------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class PlayView:
    """A card's lands, costs and spells, compiled once: everything the engine asks.

    ``untapped_if`` is the condition a land that enters tapped does not under
    (compiled: ``untapped_holds``); ``search`` its land search, ``ways`` the
    ways its additional cost can be paid, ``draw`` to ``life`` what it does
    as it resolves.
    """

    enters_tapped: bool = False
    untapped_if: Condition | None = None
    untapped_holds: Any = None
    search: LandSearch | None = None
    tutor: TutorSpec | None = None
    discard_cost: int = 0
    ways: tuple[AdditionalCost, ...] = ()
    exiled: bool = False
    draw: int = 0
    draws_x: bool = False
    discard: int = 0
    put_back: int = 0
    life: int = 0
    landers: int = 0
    #: v34: cards drawn by a count (an `Amount`), and as much life lost.
    draw_by: Any = None
    lose_drawn: bool = False


NO_PLAY = PlayView()


def compile_view(abilities) -> PlayView:
    """The engine's view of a card's blocks of lands, costs and spells."""
    if not any(each.form in FORMS for each in abilities):
        return NO_PLAY
    values = _read(abilities)
    unless = values["tapped_unless"]
    return PlayView(
        enters_tapped=values["enters_tapped"], untapped_if=unless,
        untapped_holds=check(unless) if unless is not None else None,
        search=values["land_search"], tutor=values["tutor"],
        discard_cost=values["discard_cost"], ways=values["additional_costs"],
        exiled=values["exiled_on_cast"], draw=values["draw_on_cast"],
        draws_x=values["draws_x"], discard=values["discard_on_cast"],
        put_back=values["put_back_on_cast"], life=values["life_on_cast"],
        landers=values["landers"], draw_by=values["draw_by"], lose_drawn=values["lose_drawn"])
