"""The mana family as blocks: encoder, decoder and what the engine reads (phase 4, PR 13).

Sixteen fields of the old engine card said how a card makes mana: its mana
abilities and their rules, rituals, cost reduction, staying tapped,
sacrificing for mana, tapping others, Moxen, mills, stores and flips. Each
had code of its own in the engine. PR 13 turns them into blocks
(:mod:`simulation.blocks` - an `Ability` with costs, conditions and effects)
built from the atoms in :mod:`simulation.rules`: a `Selector` where a card is
picked, an `Amount` where something is counted, a `Condition` where the
board is asked, a `Cost` atom where something is paid.

Three functions:

* `encode` turns the sixteen fields, as the adapter compiles them, into
  blocks. Nothing about the adapter changes.
* `decode` turns the blocks back into exactly those fields. The application
  reads them so until phase 5 (wording, the editor, provenance, the coverage
  snapshot, a stored deck), and the gate holds that every card of the
  catalogue comes back exactly.
* `compile_view` turns a card's blocks into a `ManaView` once: what the
  engine asks while it plays. The engine never reads the fields.

Each block names the field it came from (``form``), so the decoder knows
where it goes; the order of a card's mana abilities is their ``slot``.
"""

from dataclasses import dataclass
from typing import Any

from simulation import cards
from simulation.blocks import MANA, RESOLVES, STATIC, TRIGGERED, Ability, Cost, Effect, Event
from simulation.legacy import (
    CostReduction,
    ManaAbility,
    SpellFilter,
    Store,
    TapMana,
    TappedUnless,
)
from simulation.manacost import COLORLESS, SUBTYPE_COLORS, ManaCost, normalised
from simulation.rules.amount import Amount, compile_amount
from simulation.rules.condition import Condition, check
from simulation.rules.selector import Selector, compile_selector, wanted, wanted_of

# --- the sixteen fields ------------------------------------------------------------------

#: The fields of the family, in the order the card declared them, with their
#: defaults.
FIELDS: dict[str, Any] = {
    "mana_abilities": (),
    "ritual_gain": 0,
    "ritual_color": "B",
    "cost_reduction": None,
    "untaps": True,
    "ritual_counts": "",
    "sacrifice_mana": None,
    "sacrifice_mana_amount": 0,
    "sacrifice_mana_color": "",
    "sacrifice_mana_taps": False,
    "sacrifice_mana_other": False,
    "tap_mana": None,
    "mox": "",
    "mana_mills": 0,
    "store": None,
    "flip": 0,
}

#: The ``form`` of every block of the family.
FORMS = frozenset({"mana_abilities", "ritual", "cost_reduction", "untaps", "sacrifice_mana",
                   "tap_mana", "mox", "mana_mills", "store", "flip"})

# --- rules of a mana ability -----------------------------------------------------------

FLAT = cards.FLAT
PER_CONTROLLED = cards.PER_CONTROLLED
DOUBLE_SUBTYPE = cards.DOUBLE_SUBTYPE
TYPE_ADDING = cards.TYPE_ADDING
LANDS_COULD_PRODUCE = cards.LANDS_COULD_PRODUCE
COUNTS = cards.COUNTS
EXTRA = cards.EXTRA
MULTIPLY = cards.MULTIPLY
GRANT = cards.GRANT
FILTER = cards.FILTER

#: Each rule as the block that plays it: its kind and its verb.
_RULES = {
    FLAT: (MANA, "add_mana"),
    COUNTS: (MANA, "add_counted"),
    PER_CONTROLLED: (MANA, "add_per_land"),
    LANDS_COULD_PRODUCE: (MANA, "add_what_lands_could"),
    FILTER: (MANA, "filter_mana"),
    TYPE_ADDING: (STATIC, "add_land_type"),
    DOUBLE_SUBTYPE: (TRIGGERED, "add_on_tapped"),
    EXTRA: (TRIGGERED, "add_on_tapped"),
    MULTIPLY: (STATIC, "multiply_mana"),
    GRANT: (STATIC, "grant_mana_ability"),
}
_VERBS = {(kind, verb, event): rule for rule, (kind, verb) in _RULES.items()
          for event in ((rule,) if verb == "add_on_tapped" else (None,))}

#: The event of a block added each time a source is tapped for mana.
TAPPED_FOR_MANA = "tapped_for_mana"

# --- counting, as an amount -------------------------------------------------------------

#: A counting ability whose blank colour is "the one the hand wants".
_WANTED_WHEN_BLANK = ("power", "counters", "power:greatest", "toughness:greatest_other",
                      "enchantment")
_STATS = ("power", "counters", "power:greatest", "toughness:greatest_other")


def counted(what: str, produces: tuple = ()) -> Amount:
    """A counting ability's ``subtype`` string as an `Amount`."""
    if what == "creature":
        return Amount("count", zone="creatures")
    if what.startswith("basic:"):
        kind = what.split(":", 1)[1]
        alternatives = (Selector(land_type="basic_swamp"),) if kind == "swamp" else ()
        return Amount("count", zone="lands", selector=Selector(
            basic=True, subtypes=frozenset({kind}), alternatives=alternatives))
    if what.startswith("graveyard:"):
        return Amount("count", zone="graveyard", selector=Selector(
            types=frozenset({"creature"}), cost_color=what.split(":", 1)[1]))
    if what in _STATS:
        return Amount("stat", stat=what)
    if what.startswith("devotion:"):
        return Amount("devotion", color=what.split(":", 1)[1])
    if what == "devotion":
        return Amount("best_devotion")
    if what == "enchantment":
        return Amount("count", zone="battlefield", selector=Selector(
            types=frozenset({"enchantment"}), printed_types=True))
    if what == "creature:defender":
        return Amount("count", zone="creatures", selector=Selector(defender=True))
    if what.startswith("names:"):
        return Amount("all_named", n=sum(amount for _, amount in produces),
                      names=frozenset(what.split(":", 1)[1].split("|")))
    # An Elf, or the deck's chosen type (Three Tree City, P19 R18).
    return Amount("count", zone="creatures",
                  selector=Selector(creature_types=frozenset({what})))


def counted_as(amount: Amount, names_order: str = "") -> str:
    """`counted` backwards: the ``subtype`` string."""
    if amount.kind == "stat":
        return amount.stat
    if amount.kind == "devotion":
        return f"devotion:{amount.color}"
    if amount.kind == "best_devotion":
        return "devotion"
    if amount.kind == "all_named":
        return names_order
    selector = amount.selector
    if selector is None:
        return "creature"
    if amount.zone == "lands":
        return f"basic:{next(iter(selector.subtypes))}"
    if amount.zone == "graveyard":
        return f"graveyard:{selector.cost_color}"
    if amount.zone == "battlefield":
        return "enchantment"
    if selector.defender:
        return "creature:defender"
    return next(iter(selector.creature_types))


def ritual_counted(counts: str, gain: int) -> Amount:
    """A ritual's ``ritual_counts`` string (and its gain) as an `Amount`."""
    if counts == "creature":
        return Amount("count", zone="creatures")
    if counts.startswith("tapped:"):
        # High Tide: the Islands in this turn's pool, less the one it is cast with.
        return Amount("count", zone="untapped_lands", less=1,
                      selector=Selector(subtypes=frozenset({counts.split(":", 1)[1]})))
    if counts.startswith("named:"):
        # Rite of Flame: one more for each copy already resolved.
        return Amount("count", zone="graveyard", n=gain,
                      selector=Selector(names=frozenset({counts.split(":", 1)[1]})))
    if counts.startswith("threshold:"):
        _, needed, more = counts.split(":")
        return Amount("threshold", zone="graveyard", n=gain, at_least=int(needed),
                      more=int(more))
    return Amount("fixed", n=gain)


def ritual_counts_of(amount: Amount) -> str:
    """`ritual_counted` backwards: the ``ritual_counts`` string."""
    if amount.kind == "threshold":
        return f"threshold:{amount.at_least}:{amount.more}"
    if amount.kind == "fixed":
        return ""
    if amount.zone == "creatures":
        return "creature"
    if amount.zone == "untapped_lands":
        return f"tapped:{next(iter(amount.selector.subtypes))}"
    return f"named:{next(iter(amount.selector.names))}"


# --- spend limits, as selectors ------------------------------------------------------------


def spell_selector(spell_filter: SpellFilter) -> Selector:
    """One kind of spell restricted mana may pay for, as a selector."""
    return Selector(types=spell_filter.types, named_types=spell_filter.subtypes,
                    legendary=spell_filter.legendary, colorless=spell_filter.colorless,
                    multicolored=spell_filter.multicolored,
                    noncreature=spell_filter.noncreature, only_color=spell_filter.only_color,
                    printed_colors=True)


def spell_filter(selector: Selector) -> SpellFilter:
    return SpellFilter(types=selector.types, subtypes=selector.named_types,
                       legendary=selector.legendary, colorless=selector.colorless,
                       multicolored=selector.multicolored, noncreature=selector.noncreature,
                       only_color=selector.only_color)


# --- encoding ---------------------------------------------------------------------------------


def _args(**values) -> tuple:
    return tuple(sorted((name, value) for name, value in values.items()))


#: ManaAbility fields a block holds as atoms rather than as arguments.
_ATOM_FIELDS = ("rule", "activation_generic", "only_if", "spend_only")


def _mana_ability(ability: ManaAbility, slot: int) -> Ability:
    kind, verb = _RULES.get(ability.rule, (MANA, "add_mana"))
    costs = []
    if kind == MANA:
        costs.append(Cost("tap"))
    if ability.activation_generic:
        costs.append(Cost("mana", mana=ManaCost(generic=ability.activation_generic)))
    args = {item: getattr(ability, item) for item in ("produces", "subtype", "color",
                                                      "pays_with", "times")
            if getattr(ability, item) != getattr(_PLAIN, item)}
    if ability.spend_only:
        args["spend"] = tuple(spell_selector(each) for each in ability.spend_only)
    if ability.rule == COUNTS and ability.subtype != "colors_among":
        args["amount"] = counted(ability.subtype, ability.produces)
    elif ability.rule == PER_CONTROLLED:
        args["amount"] = Amount("count", zone="land_types",
                                selector=Selector(subtypes=frozenset({ability.subtype})))
    if ability.rule not in _RULES:
        args["rule"] = ability.rule
    return Ability(kind, "mana_abilities", slot=slot,
                   event=Event(TAPPED_FOR_MANA, filter=ability.rule)
                   if verb == "add_on_tapped" else None,
                   costs=tuple(costs),
                   conds=(Condition.of(ability.only_if),) if ability.only_if else (),
                   effects=(Effect(verb, _args(**args)),))


_PLAIN = ManaAbility(FLAT)


def encode(values: dict) -> tuple[Ability, ...]:
    """The family's fields as blocks; a field at its default makes none."""
    get = {**FIELDS, **values}.__getitem__
    found = [_mana_ability(each, slot) for slot, each in enumerate(get("mana_abilities"))]
    gain, color, counts = get("ritual_gain"), get("ritual_color"), get("ritual_counts")
    if gain or counts or color != FIELDS["ritual_color"]:
        # A ritual's colour on a card that is no ritual is a value nothing
        # reads; it is kept so that the card comes back the same.
        found.append(Ability(RESOLVES, "ritual", effects=(Effect("add_mana", _args(
            amount=ritual_counted(counts, gain), color=color, n=gain)),)))
    reduction = get("cost_reduction")
    if reduction is not None:
        found.append(Ability(STATIC, "cost_reduction", effects=(Effect("reduce_cost", _args(
            n=reduction.amount, pip=reduction.requires_pip, color=reduction.color)),)))
    if not get("untaps"):
        found.append(Ability(STATIC, "untaps", effects=(Effect("doesnt_untap"),)))
    sacrifice = get("sacrifice_mana")
    if sacrifice is not None or get("sacrifice_mana_amount") or get("sacrifice_mana_color") \
            or get("sacrifice_mana_taps") or get("sacrifice_mana_other"):
        found.append(_altar(sacrifice, get))
    tap = get("tap_mana")
    if tap is not None:
        found.append(Ability(MANA, "tap_mana", costs=(Cost(
            "tap_other", amount=1, selector=wanted(tap.types, tap.filter)),),
            effects=(Effect("add_mana", _args(
                mana=tap.mana, n=tap.amount, tokens=tap.tokens,
                spend=tuple(spell_selector(each) for each in tap.spend_only))),)))
    if get("mox"):
        found.append(Ability(STATIC, "mox", effects=(Effect("mox", _args(mode=get("mox"))),)))
    if get("mana_mills"):
        found.append(Ability(TRIGGERED, "mana_mills", event=Event(TAPPED_FOR_MANA),
                             effects=(Effect("mill", _args(n=get("mana_mills"))),)))
    store = get("store")
    if store is not None:
        found.append(Ability(MANA, "store", costs=(Cost("remove_counters",
                                                        amount=store.per_use),),
                             effects=(Effect("add_mana", _args(
                                 mana=store.mana, n=store.amount, use=store.use,
                                 energy=store.energy, start=store.start,
                                 start_from=store.start_from, grows=store.grows,
                                 empty=store.empty)),)))
    if get("flip"):
        found.append(Ability(TRIGGERED, "flip", event=Event("end_step"),
                             effects=(Effect("transform", _args(creatures=get("flip"))),)))
    return tuple(found)


def _altar(sacrifice, get) -> Ability:
    costs = [Cost("tap")] if get("sacrifice_mana_taps") else []
    if sacrifice is not None:
        costs.append(Cost("sacrifice", amount=sacrifice.sacrifice_count,
                          selector=wanted(sacrifice.sacrifice, sacrifice.sacrifice_filter),
                          other=get("sacrifice_mana_other")))
        if sacrifice.life or sacrifice.life_x:
            costs.append(Cost("life", amount=sacrifice.life, x=sacrifice.life_x))
        if sacrifice.discard:
            costs.append(Cost("discard", amount=sacrifice.discard))
        if sacrifice.mana is not None:
            costs.append(Cost("mana", mana=sacrifice.mana))
        if sacrifice.exile_from_graveyard:
            costs.append(Cost("exile_from_graveyard", amount=1,
                              filter=sacrifice.exile_from_graveyard))
    elif get("sacrifice_mana_other"):
        costs.append(Cost("nothing_sacrificed", other=True))
    return Ability(MANA, "sacrifice_mana", costs=tuple(costs), effects=(Effect("add_mana", _args(
        color=get("sacrifice_mana_color"), n=get("sacrifice_mana_amount"))),))


# --- decoding ------------------------------------------------------------------------------------


def decode(abilities) -> dict:
    """The family's fields from blocks, every one: defaults where none says."""
    out = dict(FIELDS)
    mana = sorted((each for each in abilities if each.form == "mana_abilities"),
                  key=lambda each: each.slot)
    out["mana_abilities"] = tuple(_mana_ability_of(each) for each in mana)
    for ability in abilities:
        form = ability.form
        if form not in FORMS or form == "mana_abilities":
            continue
        effect = ability.effects[0] if ability.effects else None
        if form == "ritual":
            amount = effect.arg("amount")
            out.update(ritual_gain=effect.arg("n"), ritual_color=effect.arg("color"),
                       ritual_counts=ritual_counts_of(amount))
        elif form == "cost_reduction":
            out["cost_reduction"] = CostReduction(amount=effect.arg("n"),
                                                  requires_pip=effect.arg("pip"),
                                                  color=effect.arg("color"))
        elif form == "untaps":
            out["untaps"] = False
        elif form == "sacrifice_mana":
            out.update(_altar_of(ability))
        elif form == "tap_mana":
            types, filter_ = wanted_of(ability.costs[0].selector)
            out["tap_mana"] = TapMana(types=types, filter=filter_, mana=effect.arg("mana"),
                                      amount=effect.arg("n"), tokens=effect.arg("tokens"),
                                      spend_only=tuple(spell_filter(each)
                                                       for each in effect.arg("spend")))
        elif form == "mox":
            out["mox"] = effect.arg("mode")
        elif form == "mana_mills":
            out["mana_mills"] = effect.arg("n")
        elif form == "store":
            out["store"] = Store(per_use=ability.costs[0].amount, mana=effect.arg("mana"),
                                 amount=effect.arg("n"), use=effect.arg("use"),
                                 energy=effect.arg("energy"), start=effect.arg("start"),
                                 start_from=effect.arg("start_from"), grows=effect.arg("grows"),
                                 empty=effect.arg("empty"))
        elif form == "flip":
            out["flip"] = effect.arg("creatures")
    return out


def _mana_ability_of(ability: Ability) -> ManaAbility:
    effect = ability.effects[0]
    args = dict(effect.args)
    rule = args.pop("rule", None) or _VERBS[(ability.kind, effect.verb,
                                             ability.event.filter if ability.event else None)]
    args.pop("amount", None)
    spend = args.pop("spend", ())
    mana = next((cost for cost in ability.costs if cost.atom == "mana"), None)
    condition = ability.conds[0] if ability.conds else None
    return ManaAbility(rule, **args,
                       activation_generic=mana.mana.generic if mana else 0,
                       only_if=TappedUnless(**vars(condition)) if condition else None,
                       spend_only=tuple(spell_filter(each) for each in spend))


def _altar_of(ability: Ability) -> dict:
    atoms = {cost.atom: cost for cost in ability.costs}
    effect = ability.effects[0]
    out = {"sacrifice_mana_taps": "tap" in atoms,
           "sacrifice_mana_amount": effect.arg("n"),
           "sacrifice_mana_color": effect.arg("color")}
    sacrifice = atoms.get("sacrifice")
    if sacrifice is None:
        out["sacrifice_mana_other"] = "nothing_sacrificed" in atoms
        return out
    types, filter_ = wanted_of(sacrifice.selector)
    life, mana = atoms.get("life"), atoms.get("mana")
    discard, exile = atoms.get("discard"), atoms.get("exile_from_graveyard")
    out["sacrifice_mana_other"] = sacrifice.other
    out["sacrifice_mana"] = cards.AdditionalCost(
        sacrifice=types, sacrifice_filter=filter_, sacrifice_count=sacrifice.amount,
        life=life.amount if life else 0, life_x=life.x if life else False,
        discard=discard.amount if discard else 0, mana=mana.mana if mana else None,
        exile_from_graveyard=exile.filter if exile else "")
    return out


# --- what the engine reads ----------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Source:
    """A mana ability as the engine plays it: what it makes and what it asks.

    ``rule`` is the rule it plays by (``flat``, ``counts``, ``per_controlled``,
    ``lands_could_produce``, ``filter``). A source the board makes - Chrome
    Mox's colours, a store's counters, a counted amount - is a ``flat`` one.
    ``activation_generic`` is the generic mana it costs besides {T},
    ``only_if`` the condition it asks (compiled: ``holds``), ``spend_only``
    the spells its mana may pay for (compiled: ``spends``) and ``amount``
    what a counting one counts (compiled: ``counts``).
    """

    rule: str
    produces: tuple[tuple[str, int], ...] = ()
    activation_generic: int = 0
    only_if: Condition | None = None
    spend_only: tuple[Selector, ...] = ()
    amount: Amount | None = None
    color: str = ""
    subtype: str = ""
    pays_with: str = ""
    times: int = 1
    #: The compiled condition, spend limit and amount.
    holds: Any = None
    spends: Any = None
    counts: Any = None

    @property
    def total(self) -> int:
        return sum(amount for _, amount in self.produces)

    @property
    def scaling_color(self) -> str:
        """The colour a ``per_controlled`` source makes: its own, or the
        colour of the land type it counts."""
        return self.color or SUBTYPE_COLORS.get(self.subtype, COLORLESS)

    @property
    def colored_total(self) -> int:
        """Mana made that is coloured; colourless does not count."""
        return sum(amount for source, amount in self.produces if source != COLORLESS)

    def amount_of(self, color: str) -> int:
        return dict(self.produces).get(color, 0)


def flat(produces, activation_generic: int = 0) -> Source:
    """A ``flat`` source that makes this mana, for this much generic mana."""
    return Source(FLAT, normalised(produces), activation_generic)


@dataclass(frozen=True)
class Ritual:
    """What a ritual adds as it resolves: a colour, and how much (compiled)."""

    color: str
    gain: int
    amount: Any


@dataclass(frozen=True)
class Reduction:
    """Spells cost ``amount`` less: those with a pip of ``color`` when
    ``requires_pip``, else every one."""

    amount: int
    requires_pip: bool
    color: str


@dataclass(frozen=True)
class Altar:
    """Sacrificing for mana: what it sacrifices, what that costs beside, and
    what it makes."""

    selector: Selector | None
    count: int
    life: int
    color: str
    amount: int
    taps: bool
    other: bool
    selects: Any = None

    @property
    def sacrifices(self) -> bool:
        return self.selector is not None

    @property
    def sacrifice(self) -> frozenset[str]:
        """The types it may sacrifice, as a sacrifice cost names them."""
        return wanted_of(self.selector)[0] if self.selector is not None else frozenset()

    @property
    def sacrifice_filter(self) -> str:
        return wanted_of(self.selector)[1] if self.selector is not None else ""


@dataclass(frozen=True)
class TapOther:
    """Tapping other permanents for mana: which, and what each makes."""

    selector: Selector
    mana: str
    amount: int
    spend: tuple[Selector, ...]
    tokens: int
    selects: Any = None
    spends: Any = None


@dataclass(frozen=True)
class Stored:
    """Mana kept as counters (the fields of a store, as the engine reads them)."""

    use: str
    mana: str
    amount: int
    per_use: int
    energy: bool
    start: int
    start_from: str
    grows: str
    empty: str


@dataclass(frozen=True, eq=False)
class ManaView:
    """A card's mana family, compiled once: everything the engine asks.

    ``entries`` are its mana abilities in order, each a `Source` whatever its
    rule; ``sources`` those it taps for (rules ``flat``, ``counts``,
    ``per_controlled``, ``lands_could_produce``, ``filter``) and
    ``land_types`` the land types it gives every land.
    """

    entries: tuple[Source, ...] = ()
    sources: tuple[Source, ...] = ()
    land_types: tuple[str, ...] = ()
    ritual: Ritual | None = None
    reduction: Reduction | None = None
    untaps: bool = True
    altar: Altar | None = None
    tap_other: TapOther | None = None
    mox: str = ""
    mills: int = 0
    store: Stored | None = None
    flip: int = 0
    #: The condition of its first mana ability that taps another permanent
    #: besides itself (Springleaf Drum), if it has one.
    tap_condition: Condition | None = None

    @property
    def taps_another(self) -> bool:
        return self.tap_condition is not None

    def source(self, rule: str) -> Source | None:
        """The first source of this rule."""
        return next((each for each in self.sources if each.rule == rule), None)

    def of_rule(self, rule: str) -> tuple[Source, ...]:
        """Its mana abilities of this rule, in order."""
        return tuple(each for each in self.entries if each.rule == rule)


NO_MANA = ManaView()


def _compiled_spend(selectors):
    tests = tuple(compile_selector(each) for each in selectors)
    return lambda spell: spell is not None and any(test(spell) for test in tests)


def _source(ability: Ability) -> Source:
    effect = ability.effects[0]
    rule = effect.arg("rule") or _VERBS[(ability.kind, effect.verb,
                                         ability.event.filter if ability.event else None)]
    mana = next((cost for cost in ability.costs if cost.atom == "mana"), None)
    condition = ability.conds[0] if ability.conds else None
    spend = effect.arg("spend", ())
    amount = effect.arg("amount")
    return Source(
        rule, normalised(effect.arg("produces", ())),
        activation_generic=mana.mana.generic if mana else 0, only_if=condition,
        spend_only=spend,
        amount=amount, color=effect.arg("color", ""), subtype=effect.arg("subtype", ""),
        pays_with=effect.arg("pays_with", ""), times=effect.arg("times", 1),
        holds=check(condition) if condition is not None else None,
        spends=_compiled_spend(spend) if spend else None,
        counts=compile_amount(amount) if amount is not None else None)


def compile_view(abilities) -> ManaView:
    """The engine's view of a card's mana blocks."""
    if not any(each.form in FORMS for each in abilities):
        return NO_MANA
    entries = tuple(_source(ability) for ability in sorted(
        (each for each in abilities if each.form == "mana_abilities"),
        key=lambda each: each.slot))
    found: dict[str, Any] = {}
    for ability in abilities:
        form = ability.form
        effect = ability.effects[0] if ability.effects else None
        if form == "ritual":
            found["ritual"] = Ritual(effect.arg("color"), effect.arg("n"),
                                     compile_amount(effect.arg("amount")))
        elif form == "cost_reduction":
            found["reduction"] = Reduction(effect.arg("n"), effect.arg("pip"),
                                           effect.arg("color"))
        elif form == "untaps":
            found["untaps"] = False
        elif form == "sacrifice_mana":
            found["altar"] = _compiled_altar(ability)
        elif form == "tap_mana":
            selector, spend = ability.costs[0].selector, effect.arg("spend")
            found["tap_other"] = TapOther(selector, effect.arg("mana"), effect.arg("n"), spend,
                                          effect.arg("tokens"), compile_selector(selector),
                                          _compiled_spend(spend) if spend else None)
        elif form == "mox":
            found["mox"] = effect.arg("mode")
        elif form == "mana_mills":
            found["mills"] = effect.arg("n")
        elif form == "store":
            found["store"] = Stored(effect.arg("use"), effect.arg("mana"), effect.arg("n"),
                                    ability.costs[0].amount, effect.arg("energy"),
                                    effect.arg("start"), effect.arg("start_from"),
                                    effect.arg("grows"), effect.arg("empty"))
        elif form == "flip":
            found["flip"] = effect.arg("creatures")
    found["tap_condition"] = next((each.only_if for each in entries if each.only_if is not None
                                   and each.only_if.kind == "tap_fodder"), None)
    return ManaView(entries, tuple(each for each in entries if each.rule in _SOURCE_RULES),
                    tuple(each.subtype for each in entries if each.rule == TYPE_ADDING),
                    **found)


#: The rules of a mana ability a source taps for.
_SOURCE_RULES = frozenset({FLAT, COUNTS, PER_CONTROLLED, LANDS_COULD_PRODUCE, FILTER})


def _compiled_altar(ability: Ability) -> Altar:
    atoms = {cost.atom: cost for cost in ability.costs}
    effect = ability.effects[0]
    sacrifice, life = atoms.get("sacrifice"), atoms.get("life")
    selector = sacrifice.selector if sacrifice is not None else None
    return Altar(selector, sacrifice.amount if sacrifice else 0, life.amount if life else 0,
                 effect.arg("color"), effect.arg("n"), "tap" in atoms,
                 bool(sacrifice and sacrifice.other) or "nothing_sacrificed" in atoms,
                 compile_selector(selector) if selector is not None else None)
