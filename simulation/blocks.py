"""A card as composable blocks, and back (card model rebuild, phase 0).

Today a card is one :class:`~simulation.cards.Card` of some 70 fields and
fourteen helper structures, each added by the round that needed it. The
rebuild describes the same card as a few kinds of block that compose:

* an :class:`Ability` - what kind it is (cast, static, activated, mana,
  triggered, resolves, replacement), the :class:`Event` it waits for, its
  :class:`Cost` atoms, its conditions and an ordered chain of :class:`Effect`;
* a :class:`Cost` atom - tap, mana, life, discard, sacrifice ...;
* an :class:`Effect` - one verb (``add_mana``, ``draw``, ``search``,
  ``create_token`` ...) with its arguments;
* a condition - the engine's existing question type
  (:class:`~simulation.cards.TappedUnless`), here called :data:`Cond`;
* a :class:`Face` - what is printed on the card, and the play hints that
  are a deck author's judgement rather than rules text.

**Phase 0 proves only that nothing is lost:** ``from_blocks(to_blocks(card))``
must equal ``card`` for every card the engine reads. Each ability therefore
still names the engine structure that plays it today (``form``): two
structures that say the same thing in blocks - an upkeep draw as an
``UpkeepSpec`` and as a ``Trigger`` - are what the later phases merge, and
:func:`forms_by_shape` counts them. ``form`` goes once the engine runs on
blocks.

Nothing here imports Django, like the rest of :mod:`simulation`.
"""

import dataclasses
from collections import Counter
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from simulation.cards import (
    AdditionalCost,
    Card,
    CombatTrigger,
    CostReduction,
    EndStepSpec,
    Growth,
    LandSearch,
    ManaAbility,
    Store,
    TapMana,
    TappedUnless,
    Trigger,
    TutorSpec,
    UpkeepSpec,
)
from simulation.cards import fields as card_fields
from simulation.manacost import ManaCost
from simulation.rules.selector import ANY, Selector

#: A condition block. The engine's question type is general already: it
#: guards lands entering tapped and mana abilities alike.
Cond = TappedUnless

# --- ability kinds --------------------------------------------------------------

#: How the card is cast: its additional costs.
CAST = "cast"
#: What the card does as it resolves - a spell, or a permanent arriving.
RESOLVES = "resolves"
STATIC = "static"
ACTIVATED = "activated"
#: An activated ability that makes mana: the payment search plays these.
MANA = "mana"
TRIGGERED = "triggered"
#: Instead of something else happening: Mox Diamond, a Spirit Guide.
REPLACEMENT = "replacement"

KINDS = (CAST, RESOLVES, STATIC, ACTIVATED, MANA, TRIGGERED, REPLACEMENT)

# --- verbs a source may use that no engine structure plays -----------------------

#: A construct of the source outside the vocabulary: ``construct`` names it.
#: Never dropped silently - a card holding one is read only in part.
UNMODELED = "unmodeled"
#: A keyword ability (Flying, Equip): ``name``, and ``args`` as printed.
KEYWORD = "keyword"
#: "Choose one -": ``n`` modes of ``of``; the modes are the abilities with
#: ``mode`` 1 and up.
CHOOSE = "choose"

#: The version of the block format, stored beside every block written.
#: 2: an effect's ``what`` is a tuple of `Which`, not a source's string
#: (card model phase 5).
VERSION = 2


def changed_args(value, *, skip=()) -> tuple[tuple[str, Any], ...]:
    """A dataclass's fields that differ from their defaults, sorted by name."""
    out = []
    for item in dataclasses.fields(value):
        if item.name in skip:
            continue
        current = getattr(value, item.name)
        if item.default is not dataclasses.MISSING:
            default = item.default
        elif item.default_factory is not dataclasses.MISSING:
            default = item.default_factory()
        else:
            default = dataclasses.MISSING
        if current != default:
            out.append((item.name, current))
    return tuple(sorted(out))


@dataclass(frozen=True)
class Cost:
    """One atom of a cost.

    Attributes:
        atom: ``tap``, ``mana``, ``life``, ``discard``, ``sacrifice_self``,
            ``sacrifice`` (another permanent), ``sacrifice_land``,
            ``exile_from_graveyard`` or ``tap_other``.
        amount: How many - life, cards, permanents.
        mana: For ``mana``: what is paid.
        types: What a sacrificed, exiled or tapped permanent may be, any of
            them.
        filter: What it must also be: a colour letter, ``legendary``, a
            creature type.
        x: The amount is X (``life`` of Toxic Deluge).
        other: Never the card itself.
        selector: See the field.
    """

    atom: str
    amount: int = 0
    mana: ManaCost | None = None
    types: frozenset[str] = field(default_factory=frozenset)
    filter: str = ""
    x: bool = False
    other: bool = False
    #: What a tapped or sacrificed permanent must be, as one selector (card
    #: model phase 4, :mod:`simulation.rules.selector`); the mana family's
    #: blocks use it in place of ``types`` and ``filter``.
    selector: Any = None


@dataclass(frozen=True)
class Event:
    """What a triggered ability waits for: ``upkeep``, ``landfall``, ``cast``,
    ``attacks`` ... ``filter`` and ``who`` narrow it; ``types`` are the
    creature or card types it is about."""

    name: str
    filter: str = ""
    who: str = ""
    types: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Which:
    """Which cards an effect is about, as a source reads them (card model
    phase 5): one alternative of an effect's ``what``.

    What the cards must be is the engine's own `Selector`; the rest names
    cards by where they stand to the ability, which a block reads but the
    engine never plays.

    Attributes:
        cards: What they must be: types, subtypes, basic, colour, names.
        base: The kind of card named first (``creature``, ``land``,
            ``forest``), or ``card`` / ``permanent`` for any; "" when the
            alternative names a card by reference only.
        whose: ``you`` or ``opponent``: who controls or owns them.
        itself: The card itself.
        other: Never the card itself.
        found: Cards the ability or its trigger already found, by how
            (``remembered``, ``triggered``, ``targeted``, ``chosen``,
            ``imprinted``, ``top_of_library``).
        not_types: Types they must not have ("nonland permanent").
        each: One of each alternative, not one of them ("a Forest and a
            Plains").
        unread: A source's words no block holds yet ("cmcLE3").
    """

    cards: Selector = ANY
    base: str = ""
    whose: str = ""
    itself: bool = False
    other: bool = False
    found: frozenset[str] = field(default_factory=frozenset)
    not_types: frozenset[str] = field(default_factory=frozenset)
    each: bool = False
    unread: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Effect:
    """One verb with its arguments, sorted by name so equal effects compare equal."""

    verb: str
    args: tuple[tuple[str, Any], ...] = ()

    def arg(self, name: str, default=None):
        return dict(self.args).get(name, default)


@dataclass(frozen=True)
class Ability:
    """One ability: its kind, its trigger, its costs, its conditions, its effects.

    ``options`` are alternative cost sets, any one of which will do ("discard
    a card or pay 3 life"). ``form`` names today's engine structure that plays
    it (see the module docstring), empty for an ability read from a source
    that no structure plays yet. ``slot`` numbers the abilities of one kind
    on the card, in order; with the kind it is the ability's stable id.
    """

    kind: str
    form: str
    effects: tuple[Effect, ...] = ()
    costs: tuple[Cost, ...] = ()
    options: tuple[tuple[Cost, ...], ...] = ()
    conds: tuple[Cond, ...] = ()
    event: Event | None = None
    slot: int = 0
    #: Of a modal spell ("choose one"): which mode, from 1; 0 for no mode.
    mode: int = 0
    #: What the source says about the ability that no block holds yet - a
    #: condition, a count - by its name in the source. Not empty: the ability
    #: is read only in part.
    unread: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.slot}"

    @property
    def shape(self) -> tuple:
        """What the ability does, without which structure plays it or where it sits."""
        return (self.kind, self.event, self.costs, self.options, self.conds, self.effects)


#: Printed on the card: what a rule may ask about it.
FACE_FIELDS = (
    "name", "mv", "pips", "generic", "kind", "cost", "types", "subtypes",
    "printed_subtypes", "creature_types", "land_type", "basic", "legendary", "colors",
    "power", "toughness", "defender", "haste", "enchants", "token", "x_count", "x_max",
)

#: A deck author's judgement, not rules text: kept beside the blocks.
HINT_FIELDS = (
    "engine_roles", "categories", "goldfish_castable", "needs_creature_in_yard",
    "needs_creature_on_bf", "priority", "accelerant", "x_min",
)


@dataclass(frozen=True)
class Face:
    """The printed characteristics and the play hints, as today's fields.

    ``extra`` holds a value a field carries although nothing reads it: the
    colour of a ritual on a card that is no ritual. Kept so the round trip is
    exact; counted, so they can be dropped.
    """

    printed: tuple[tuple[str, Any], ...]
    hints: tuple[tuple[str, Any], ...] = ()
    extra: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class CardBlocks:
    face: Face
    abilities: tuple[Ability, ...] = ()

    def ability(self, ability_id: str) -> Ability | None:
        return next((found for found in self.abilities if found.id == ability_id), None)


# --- costs ------------------------------------------------------------------------


def _additional_cost_atoms(value: AdditionalCost) -> tuple[Cost, ...]:
    atoms = []
    if value.sacrifice or value.sacrifice_filter:
        atoms.append(Cost("sacrifice", amount=value.sacrifice_count, types=value.sacrifice,
                          filter=value.sacrifice_filter))
    if value.life or value.life_x:
        atoms.append(Cost("life", amount=value.life, x=value.life_x))
    if value.discard:
        atoms.append(Cost("discard", amount=value.discard))
    if value.mana is not None:
        atoms.append(Cost("mana", mana=value.mana))
    if value.exile_from_graveyard:
        atoms.append(Cost("exile_from_graveyard", amount=1,
                          filter=value.exile_from_graveyard))
    return tuple(atoms)


def _additional_cost(atoms: tuple[Cost, ...]) -> AdditionalCost:
    kwargs: dict[str, Any] = {}
    for atom in atoms:
        if atom.atom == "sacrifice":
            kwargs.update(sacrifice=atom.types, sacrifice_filter=atom.filter,
                          sacrifice_count=atom.amount or 1)
        elif atom.atom == "life":
            kwargs.update(life=atom.amount, life_x=atom.x)
        elif atom.atom == "discard":
            kwargs["discard"] = atom.amount
        elif atom.atom == "mana":
            kwargs["mana"] = atom.mana
        elif atom.atom == "exile_from_graveyard":
            kwargs["exile_from_graveyard"] = atom.filter
    return AdditionalCost(**kwargs)


def nets_mana(ability: Ability, effect: Effect) -> bool:
    """Whether ``effect`` makes more mana than ``ability``'s cost takes: a
    Signet does, a filter that turns {1} into one of any colour does not."""
    paid = sum(cost.mana.generic + cost.mana.colorless + sum(n for _, n in cost.mana.pips)
               + len(cost.mana.hybrid) + len(cost.mana.phyrexian)
               for cost in ability.costs if cost.atom == "mana" and cost.mana is not None)
    if not paid:
        return True
    n = effect.arg("n", 1)
    made = (n if isinstance(n, int) else 1) * (len(effect.arg("color", "")) if effect.arg(
        "all") else 1)
    return made > paid


def _atoms(costs: tuple[Cost, ...]) -> dict[str, Cost]:
    return {atom.atom: atom for atom in costs}


# --- one encoder and one decoder per engine structure --------------------------
#
# An encoder takes the card and returns its abilities of that form; a decoder
# takes those abilities back and returns the Card fields they stand for.


def _enc_mana(card):
    for value in card.mana_abilities:
        costs = [Cost("tap")]
        if value.activation_generic:
            costs.append(Cost("mana", mana=ManaCost(generic=value.activation_generic)))
        yield Ability(MANA, "mana", costs=tuple(costs),
                      conds=(value.only_if,) if value.only_if else (),
                      effects=(Effect("add_mana", changed_args(value, skip=("activation_generic",
                                                                     "only_if"))),))


def _dec_mana(abilities):
    found = []
    for ability in abilities:
        mana = _atoms(ability.costs).get("mana")
        found.append(ManaAbility(
            **dict(ability.effects[0].args),
            activation_generic=mana.mana.generic if mana else 0,
            only_if=ability.conds[0] if ability.conds else None))
    return {"mana_abilities": tuple(found)}


def _enc_no_untap(card):
    if not card.untaps:
        yield Ability(STATIC, "no_untap", effects=(Effect("doesnt_untap"),))


def _dec_no_untap(abilities):
    return {"untaps": False}


def _enc_mana_mills(card):
    if card.mana_mills:
        yield Ability(TRIGGERED, "mana_mills", event=Event("tapped_for_mana"),
                      effects=(Effect("mill", (("n", card.mana_mills),)),))


def _dec_mana_mills(abilities):
    return {"mana_mills": abilities[0].effects[0].arg("n")}


def _enc_ritual(card):
    if card.ritual_gain or card.ritual_counts:
        args = [("color", card.ritual_color), ("n", card.ritual_gain)]
        if card.ritual_counts:
            args.append(("counts", card.ritual_counts))
        yield Ability(RESOLVES, "ritual", effects=(Effect("add_mana", tuple(sorted(args))),))


def _dec_ritual(abilities):
    effect = abilities[0].effects[0]
    return {"ritual_gain": effect.arg("n"), "ritual_color": effect.arg("color"),
            "ritual_counts": effect.arg("counts", "")}


def _enc_cast_costs(card):
    costs = (Cost("discard", amount=card.discard_cost),) if card.discard_cost else ()
    options = tuple(_additional_cost_atoms(value) for value in card.additional_costs)
    if costs or options:
        yield Ability(CAST, "cast_costs", costs=costs, options=options)


def _dec_cast_costs(abilities):
    ability = abilities[0]
    discard = _atoms(ability.costs).get("discard")
    return {"discard_cost": discard.amount if discard else 0,
            "additional_costs": tuple(_additional_cost(option) for option in ability.options)}


#: The resolution chain of the old per-card counters, in the order the engine
#: plays them: draw (and X), discard, put back, life, Treasures, Landers.
_RESOLVE_FIELDS = (
    ("draw_on_cast", "draw"), ("discard_on_cast", "discard"),
    ("put_back_on_cast", "put_back"), ("life_on_cast", "life"),
)


def _enc_resolve(card):
    effects = []
    if card.draw_on_cast or card.draws_x:
        args = [("n", card.draw_on_cast)] + ([("x", True)] if card.draws_x else [])
        effects.append(Effect("draw", tuple(args)))
    for name, verb in _RESOLVE_FIELDS[1:]:
        if getattr(card, name):
            effects.append(Effect(verb, (("n", getattr(card, name)),)))
    if card.treasures:
        effects.append(Effect("create_token", (("mana", card.treasure_mana),
                                               ("n", card.treasures), ("token", "treasure"))))
    if card.landers:
        effects.append(Effect("create_token", (("n", card.landers), ("token", "lander"))))
    if effects:
        yield Ability(RESOLVES, "resolve", effects=tuple(effects))


def _dec_resolve(abilities):
    out: dict[str, Any] = {}
    verbs = dict(_RESOLVE_FIELDS)
    by_verb = {verb: name for name, verb in verbs.items()}
    for effect in abilities[0].effects:
        if effect.verb == "draw":
            out["draw_on_cast"] = effect.arg("n")
            out["draws_x"] = effect.arg("x", False)
        elif effect.verb == "create_token" and effect.arg("token") == "treasure":
            out["treasures"] = effect.arg("n")
            out["treasure_mana"] = effect.arg("mana")
        elif effect.verb == "create_token":
            out["landers"] = effect.arg("n")
        else:
            out[by_verb[effect.verb]] = effect.arg("n")
    return out


def _enc_tutor(card):
    value = card.tutor
    if value is None:
        return
    costs = (Cost("life", amount=value.life),) if value.life else ()
    event = Event("upkeep", filter=value.upkeep) if value.upkeep else None
    kind = TRIGGERED if event else RESOLVES
    yield Ability(kind, "tutor", costs=costs, event=event,
                  effects=(Effect("search", changed_args(value, skip=("life", "upkeep"))),))


def _dec_tutor(abilities):
    ability = abilities[0]
    life = _atoms(ability.costs).get("life")
    return {"tutor": TutorSpec(**dict(ability.effects[0].args),
                               life=life.amount if life else 0,
                               upkeep=ability.event.filter if ability.event else "")}


def _enc_cost_reduction(card):
    if card.cost_reduction is not None:
        yield Ability(STATIC, "cost_reduction",
                      effects=(Effect("reduce_cost",
                                      changed_args(card.cost_reduction) + (("set", True),)),))


def _dec_cost_reduction(abilities):
    args = dict(abilities[0].effects[0].args)
    args.pop("set")
    return {"cost_reduction": CostReduction(**args)}


def _enc_upkeep(card):
    value = card.upkeep
    if value is None:
        return
    effects = [Effect("draw", (("n", value.draw),))]
    if value.life or value.life_per_mv:
        effects.append(Effect("lose_life", tuple(sorted(
            [("n", value.life)] + ([("per", "drawn_mv")] if value.life_per_mv else [])))))
    yield Ability(TRIGGERED, "upkeep", event=Event("upkeep"), effects=tuple(effects))


def _dec_upkeep(abilities):
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


def _dec_end_step(abilities):
    args = dict(abilities[0].effects[0].args)
    args.pop("set")
    return {"end_step": EndStepSpec(**args)}


def _enc_skips_draw(card):
    if card.skips_draw_step:
        yield Ability(STATIC, "skips_draw", effects=(Effect("skip_draw_step"),))


def _dec_skips_draw(abilities):
    return {"skips_draw_step": True}


def _land_search_costs(value: LandSearch) -> tuple[Cost, ...]:
    costs = []
    if value.taps:
        costs.append(Cost("tap"))
    if value.cost is not None:
        costs.append(Cost("mana", mana=value.cost))
    if value.life:
        costs.append(Cost("life", amount=value.life))
    if value.sacrifice:
        costs.append(Cost("sacrifice_self"))
    if value.sacrifices_land:
        costs.append(Cost("sacrifice_land", amount=1, types=value.land_cost_types))
    if value.sacrifice_other:
        costs.append(Cost("sacrifice", amount=1, types=value.sacrifice_other, other=True))
    if value.discard:
        costs.append(Cost("discard", amount=value.discard))
    return tuple(costs)


_LAND_SEARCH_COST_FIELDS = ("taps", "cost", "life", "sacrifice", "sacrifices_land",
                            "land_cost_types", "sacrifice_other", "discard", "when",
                            "condition")

#: ``LandSearch.when`` -> the ability's kind and event.
LAND_SEARCH_WHEN = {
    "cast": (RESOLVES, None),
    "enters": (TRIGGERED, "enters_self"),
    "play": (TRIGGERED, "played"),
    "activate": (ACTIVATED, None),
    "combat": (TRIGGERED, "combat"),
}


def _land_search_ability(value: LandSearch, form: str) -> Ability:
    kind, event = LAND_SEARCH_WHEN.get(value.when, (TRIGGERED, value.when))
    return Ability(kind, form, costs=_land_search_costs(value),
                   event=Event(event) if event else None,
                   conds=(Cond(kind=value.condition),) if value.condition else (),
                   effects=(Effect("search_land", changed_args(value, skip=_LAND_SEARCH_COST_FIELDS)
                                   + (("when", value.when),)),))


def _land_search_value(ability: Ability) -> LandSearch:
    atoms = _atoms(ability.costs)
    args = dict(ability.effects[0].args)
    land = atoms.get("sacrifice_land")
    return LandSearch(
        **args,
        taps="tap" in atoms,
        cost=atoms["mana"].mana if "mana" in atoms else None,
        life=atoms["life"].amount if "life" in atoms else 0,
        sacrifice="sacrifice_self" in atoms,
        sacrifices_land=land is not None,
        land_cost_types=land.types if land else frozenset(),
        sacrifice_other=atoms["sacrifice"].types if "sacrifice" in atoms else frozenset(),
        discard=atoms["discard"].amount if "discard" in atoms else 0,
        condition=ability.conds[0].kind if ability.conds else "",
    )


def _enc_land_search(card):
    if card.land_search is not None:
        yield _land_search_ability(card.land_search, "land_search")


def _dec_land_search(abilities):
    return {"land_search": _land_search_value(abilities[0])}


def _enc_enters_tapped(card):
    if card.enters_tapped or card.tapped_unless is not None:
        yield Ability(STATIC, "enters_tapped",
                      conds=(card.tapped_unless,) if card.tapped_unless else (),
                      effects=(Effect("enters_tapped",
                                      () if card.enters_tapped else (("off", True),)),))


def _dec_enters_tapped(abilities):
    ability = abilities[0]
    return {"enters_tapped": not ability.effects[0].arg("off", False),
            "tapped_unless": ability.conds[0] if ability.conds else None}


def _enc_exiled_on_cast(card):
    if card.exiled_on_cast:
        yield Ability(REPLACEMENT, "exiled_on_cast", effects=(Effect("exile_instead"),))


def _dec_exiled_on_cast(abilities):
    return {"exiled_on_cast": True}


def _enc_mox(card):
    if card.mox:
        yield Ability(REPLACEMENT, "mox", effects=(Effect("mox", (("mode", card.mox),)),))


def _dec_mox(abilities):
    return {"mox": abilities[0].effects[0].arg("mode")}


def _enc_sacrifice_mana(card):
    if card.sacrifice_mana is None:
        return
    costs = list(_additional_cost_atoms(card.sacrifice_mana))
    if card.sacrifice_mana_taps:
        costs.insert(0, Cost("tap"))
    if card.sacrifice_mana_other:
        costs = [dataclasses.replace(atom, other=True) if atom.atom == "sacrifice" else atom
                 for atom in costs]
    yield Ability(MANA, "sacrifice_mana", costs=tuple(costs),
                  effects=(Effect("add_mana", (("color", card.sacrifice_mana_color),
                                               ("n", card.sacrifice_mana_amount))),))


def _dec_sacrifice_mana(abilities):
    ability = abilities[0]
    atoms = _atoms(ability.costs)
    sacrifice = atoms.get("sacrifice")
    plain = tuple(dataclasses.replace(atom, other=False) for atom in ability.costs
                  if atom.atom != "tap")
    return {"sacrifice_mana": _additional_cost(plain),
            "sacrifice_mana_taps": "tap" in atoms,
            "sacrifice_mana_other": bool(sacrifice and sacrifice.other),
            "sacrifice_mana_amount": ability.effects[0].arg("n"),
            "sacrifice_mana_color": ability.effects[0].arg("color")}


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
        # A trigger that does nothing the engine plays still holds its place.
        yield Ability(TRIGGERED, "trigger", event=Event(value.event, filter=value.filter),
                      effects=effects or (Effect("nothing", changed_args(value, skip=("event",
                                                                               "filter"))),))


def _dec_triggers(abilities):
    found = []
    for ability in abilities:
        kwargs: dict[str, Any] = {"event": ability.event.name, "filter": ability.event.filter}
        for effect in ability.effects:
            if effect.verb == "create_token":
                kwargs.update(treasures=effect.arg("n"), treasure_mana=effect.arg("mana"))
            elif effect.verb == "add_mana":
                kwargs.update(mana=effect.arg("n"), mana_color=effect.arg("color"))
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
        if value.connive:
            effects.append(Effect("connive", (("n", value.connive),)))
        yield Ability(TRIGGERED, "combat",
                      event=Event(value.event, who=value.who, types=value.types),
                      effects=tuple(effects))


def _dec_combat(abilities):
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
            else:
                kwargs.update(search=effect.arg("search"), untapped=effect.arg("untapped"))
        found.append(CombatTrigger(**kwargs))
    return {"combat": tuple(found)}


def _enc_tap_mana(card):
    value = card.tap_mana
    if value is None:
        return
    yield Ability(MANA, "tap_mana",
                  costs=(Cost("tap_other", amount=1, types=value.types, filter=value.filter),),
                  effects=(Effect("add_mana", changed_args(value, skip=("types", "filter"))
                                  + (("set", True),)),))


def _dec_tap_mana(abilities):
    ability = abilities[0]
    args = dict(ability.effects[0].args)
    args.pop("set")
    return {"tap_mana": TapMana(**args, types=ability.costs[0].types,
                                filter=ability.costs[0].filter)}


def _enc_store(card):
    if card.store is not None:
        yield Ability(MANA, "store", effects=(Effect("stored_mana",
                                                     changed_args(card.store) + (("set", True),)),))


def _dec_store(abilities):
    args = dict(abilities[0].effects[0].args)
    args.pop("set")
    return {"store": Store(**args)}


def _enc_growth(card):
    if card.growth is not None:
        value = card.growth
        effects = [Effect("counters", changed_args(value, skip=("adapt", "adapt_cost"))
                          + (("set", True),))]
        if value.adapt or value.adapt_cost is not None:
            effects.append(Effect("adapt", (("n", value.adapt),)))
        costs = (Cost("mana", mana=value.adapt_cost),) if value.adapt_cost else ()
        yield Ability(STATIC, "growth", costs=costs, effects=tuple(effects))


def _dec_growth(abilities):
    ability = abilities[0]
    args = dict(ability.effects[0].args)
    args.pop("set")
    adapt = next((effect for effect in ability.effects if effect.verb == "adapt"), None)
    mana = _atoms(ability.costs).get("mana")
    return {"growth": Growth(**args, adapt=adapt.arg("n") if adapt else 0,
                             adapt_cost=mana.mana if mana else None)}


def _enc_flip(card):
    if card.flip:
        yield Ability(TRIGGERED, "flip", event=Event("end_step"),
                      conds=(Cond(kind="creatures", count=card.flip),),
                      effects=(Effect("transform"),))


def _dec_flip(abilities):
    return {"flip": abilities[0].conds[0].count}


def _enc_equip(card):
    if card.equip is not None:
        yield Ability(ACTIVATED, "equip", costs=(Cost("mana", mana=card.equip),),
                      effects=(Effect("attach"),))


def _dec_equip(abilities):
    return {"equip": abilities[0].costs[0].mana}


def _enc_germ(card):
    if card.germ:
        yield Ability(TRIGGERED, "germ", event=Event("enters_self"),
                      effects=(Effect("create_token", (("attach", True), ("n", 1),
                                                       ("token", "germ"))),))


def _dec_germ(abilities):
    return {"germ": True}


def _enc_treasure_bonus(card):
    if card.treasure_bonus:
        yield Ability(STATIC, "treasure_bonus",
                      effects=(Effect("more_mana", (("from", "treasure"),
                                                    ("n", card.treasure_bonus))),))


def _dec_treasure_bonus(abilities):
    return {"treasure_bonus": abilities[0].effects[0].arg("n")}


#: Every engine structure, in the order a card's abilities are listed.
FORMS = {
    "mana": (_enc_mana, _dec_mana),
    "sacrifice_mana": (_enc_sacrifice_mana, _dec_sacrifice_mana),
    "tap_mana": (_enc_tap_mana, _dec_tap_mana),
    "store": (_enc_store, _dec_store),
    "no_untap": (_enc_no_untap, _dec_no_untap),
    "mana_mills": (_enc_mana_mills, _dec_mana_mills),
    "cast_costs": (_enc_cast_costs, _dec_cast_costs),
    "ritual": (_enc_ritual, _dec_ritual),
    "resolve": (_enc_resolve, _dec_resolve),
    "tutor": (_enc_tutor, _dec_tutor),
    "land_search": (_enc_land_search, _dec_land_search),
    "enters_tapped": (_enc_enters_tapped, _dec_enters_tapped),
    "cost_reduction": (_enc_cost_reduction, _dec_cost_reduction),
    "upkeep": (_enc_upkeep, _dec_upkeep),
    "end_step": (_enc_end_step, _dec_end_step),
    "skips_draw": (_enc_skips_draw, _dec_skips_draw),
    "exiled_on_cast": (_enc_exiled_on_cast, _dec_exiled_on_cast),
    "mox": (_enc_mox, _dec_mox),
    "triggers": (_enc_triggers, _dec_triggers),
    "combat": (_enc_combat, _dec_combat),
    "growth": (_enc_growth, _dec_growth),
    "flip": (_enc_flip, _dec_flip),
    "equip": (_enc_equip, _dec_equip),
    "germ": (_enc_germ, _dec_germ),
    "treasure_bonus": (_enc_treasure_bonus, _dec_treasure_bonus),
}

#: A form's name as an ability carries it -> its key in `FORMS`.
_FORM_KEY = {"trigger": "triggers"}

#: Fields a value can sit in although no rule reads it there (see `Face`).
_RITUAL_COLOR_DEFAULT = "B"


def to_blocks(card: Card) -> CardBlocks:
    """The card as blocks. Lossless: `from_blocks` gives the same card back."""
    printed = tuple((name, getattr(card, name)) for name in FACE_FIELDS)
    hints = tuple((name, getattr(card, name)) for name in HINT_FIELDS)
    # The phase 0 forms read the fields the card was made with.
    card = SimpleNamespace(**dict(printed), **dict(hints), **card_fields(card))
    extra = ()
    if not (card.ritual_gain or card.ritual_counts) and \
            card.ritual_color != _RITUAL_COLOR_DEFAULT:
        extra = (("ritual_color", card.ritual_color),)

    abilities = []
    slots: Counter = Counter()
    for encode, _decode in FORMS.values():
        for ability in encode(card):
            abilities.append(dataclasses.replace(ability, slot=slots[ability.kind]))
            slots[ability.kind] += 1
    return CardBlocks(Face(printed, hints, extra), tuple(abilities))


def from_blocks(blocks: CardBlocks) -> Card:
    """The engine's card, from blocks."""
    kwargs = dict(blocks.face.printed) | dict(blocks.face.hints) | dict(blocks.face.extra)
    grouped: dict[str, list[Ability]] = {}
    for ability in blocks.abilities:
        grouped.setdefault(_FORM_KEY.get(ability.form, ability.form), []).append(ability)
    for form, abilities in grouped.items():
        kwargs.update(FORMS[form][1](abilities))
    return Card(**kwargs)


# --- what the blocks say about today's structures -------------------------------


def verbs(blocks: CardBlocks) -> Counter:
    """Effect verbs, cost atoms and events used, as ``verb:x``, ``cost:x``, ``event:x``."""
    found: Counter = Counter()
    for ability in blocks.abilities:
        found[f"kind:{ability.kind}"] += 1
        found.update(f"verb:{effect.verb}" for effect in ability.effects)
        found.update(f"cost:{atom.atom}" for atom in ability.costs)
        found.update(f"cost:{atom.atom}" for option in ability.options for atom in option)
        if ability.event:
            found[f"event:{ability.event.name}"] += 1
    return found


def forms_by_shape(cards: list[CardBlocks]) -> dict[tuple, set[str]]:
    """Ability shapes that more than one engine structure plays: what merges."""
    by_shape: dict[tuple, set[str]] = {}
    for blocks in cards:
        for ability in blocks.abilities:
            by_shape.setdefault(ability.shape, set()).add(ability.form)
    return {shape: forms for shape, forms in by_shape.items() if len(forms) > 1}


# --- stable ids: a player's correction after the card changed -------------------
#
# A correction is a patch on one ability, addressed by its id (kind and slot).
# When the card's blocks change - a source fixed a reading, an ability was
# added in front - each old id is looked up again before the patch is applied.

#: The ability is there unchanged, under the same id.
SAME = "same"
#: Unchanged, but under another id: something was added or removed before it.
MOVED = "moved"
#: Still recognisably the same ability, but it reads otherwise now: a patch
#: on it is shown as "please check", never applied silently nor dropped.
CHANGED = "changed"
#: Nothing like it is left.
GONE = "gone"


def signature(ability: Ability) -> tuple:
    """What makes two versions of an ability the same one: its kind, what it
    waits for, which cost atoms it has and which verbs it plays - not the
    numbers in them."""
    return (ability.kind, ability.event.name if ability.event else "",
            tuple(sorted({atom.atom for atom in ability.costs})),
            tuple(effect.verb for effect in ability.effects))


def reconcile(old: CardBlocks, new: CardBlocks) -> dict[str, tuple[str | None, str]]:
    """For each ability id of `old`: the id it has in `new`, and how it fared.

    Exact matches are claimed first, the same id before any other; only then
    is an ability that changed matched by `signature`, again the same id first.
    An ability two old ones could claim goes to the first.
    """
    result: dict[str, tuple[str | None, str]] = {}
    free = {ability.id: ability for ability in new.abilities}

    def claim(ability, test):
        candidate = free.get(ability.id)
        if candidate is not None and test(candidate):
            return free.pop(ability.id)
        for key, candidate in list(free.items()):
            if test(candidate):
                return free.pop(key)
        return None

    pending = []
    for ability in old.abilities:
        found = claim(ability, lambda other, a=ability: other.shape == a.shape)
        if found is None:
            pending.append(ability)
        else:
            result[ability.id] = (found.id, SAME if found.id == ability.id else MOVED)
    for ability in pending:
        found = claim(ability, lambda other, a=ability: signature(other) == signature(a))
        result[ability.id] = (found.id, CHANGED) if found else (None, GONE)
    return result


# --- stored: blocks as JSON -------------------------------------------------------
#
# Blocks a source gives (`cards.forge_import`) are stored as JSON. Conditions
# are not stored yet: no source writes them before phase 2.


def _plain(value):
    if isinstance(value, Which | Selector):
        return {"_which" if isinstance(value, Which) else "_cards": True,
                **{name: _plain(item) for name, item in changed_args(value)}}
    if isinstance(value, frozenset | set):
        return sorted(value)
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _tupled(value):
    if isinstance(value, dict):
        return _which(value)
    return tuple(_tupled(item) for item in value) if isinstance(value, list) else value


def _which(data: dict):
    """A `Which` or its `Selector`, as `_plain` wrote them."""
    kwargs = {key: frozenset(item) if isinstance(item, list) else
              _which(item) if isinstance(item, dict) else item
              for key, item in data.items() if key not in ("_which", "_cards")}
    return Which(**kwargs) if data.get("_which") else Selector(**kwargs)


def _cost_json(cost: Cost) -> dict:
    out = {name: _plain(value) for name, value in changed_args(cost)}
    if cost.mana is not None:
        out["mana"] = str(cost.mana)
    return out


def _cost_from(data: dict) -> Cost:
    from simulation.manacost import parse

    kwargs = dict(data)
    if "mana" in kwargs:
        kwargs["mana"] = parse(kwargs["mana"])
    if "types" in kwargs:
        kwargs["types"] = frozenset(kwargs["types"])
    return Cost(**kwargs)


def to_json(abilities) -> list[dict]:
    """Abilities as JSON-ready dicts, defaults left out."""
    out = []
    for ability in abilities:
        if ability.conds:
            raise ValueError(f"conditions are not stored yet: {ability.id}")
        entry = {name: value for name, value in changed_args(ability, skip=(
            "effects", "costs", "options", "event", "conds"))}
        if ability.effects:
            entry["effects"] = [{"verb": effect.verb,
                                 **({"args": [[key, _plain(value)] for key, value in effect.args]}
                                    if effect.args else {})}
                                for effect in ability.effects]
        if ability.costs:
            entry["costs"] = [_cost_json(cost) for cost in ability.costs]
        if ability.options:
            entry["options"] = [[_cost_json(cost) for cost in option]
                                for option in ability.options]
        if ability.event is not None:
            entry["event"] = {name: _plain(value) for name, value in changed_args(ability.event)}
            entry["event"]["name"] = ability.event.name
        if "unread" in entry:
            entry["unread"] = list(entry["unread"])
        out.append(entry)
    return out


def from_json(data: list[dict]) -> tuple[Ability, ...]:
    """`to_json`, backwards."""
    found = []
    for entry in data:
        kwargs = {key: value for key, value in entry.items()
                  if key not in ("effects", "costs", "options", "event", "unread")}
        kwargs["effects"] = tuple(
            Effect(effect["verb"], tuple((key, _tupled(value))
                                         for key, value in effect.get("args", ())))
            for effect in entry.get("effects", ()))
        kwargs["costs"] = tuple(_cost_from(cost) for cost in entry.get("costs", ()))
        kwargs["options"] = tuple(tuple(_cost_from(cost) for cost in option)
                                  for option in entry.get("options", ()))
        if "event" in entry:
            event = dict(entry["event"])
            if "types" in event:
                event["types"] = frozenset(event["types"])
            kwargs["event"] = Event(**event)
        kwargs["unread"] = tuple(entry.get("unread", ()))
        found.append(Ability(**kwargs))
    return tuple(found)
