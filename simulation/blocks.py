"""A card's rules as composable blocks (card model rebuild, phase 0 to 5).

A `~simulation.cards.Card` holds what is printed on it, its play hints and
its rules as a few kinds of block that compose:

* an :class:`Ability` - what kind it is (cast, static, activated, mana,
  triggered, resolves, replacement), the :class:`Event` it waits for, its
  :class:`Cost` atoms, its conditions and an ordered chain of :class:`Effect`;
* a :class:`Cost` atom - tap, mana, life, discard, sacrifice ...;
* an :class:`Effect` - one verb (``add_mana``, ``draw``, ``search``,
  ``create_token`` ...) with its arguments;
* a condition - the engine's question type,
  :class:`~simulation.rules.condition.Condition`.

Each ability still names the engine structure that plays it (``form``),
which the views of :mod:`simulation.rules` compile it into. A card's
blocks are made by :mod:`simulation.build`; phase 0's round trip back to
the fields a card was made with retired in phase 5, with the decoder.

Nothing here imports Django, like the rest of :mod:`simulation`.
"""

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from simulation.manacost import ManaCost
from simulation.rules.condition import Condition
from simulation.rules.selector import ANY, Selector

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
    conds: tuple[Condition, ...] = ()
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


# --- what a block says -----------------------------------------------------------


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


#: ``LandSearch.when`` -> the ability's kind and event.
LAND_SEARCH_WHEN = {
    "cast": (RESOLVES, None),
    "enters": (TRIGGERED, "enters_self"),
    "play": (TRIGGERED, "played"),
    "activate": (ACTIVATED, None),
    "combat": (TRIGGERED, "combat"),
}


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
