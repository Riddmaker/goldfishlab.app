"""Which cards or permanents something is about: one selector (card model, phase 4).

Before phase 4 the engine asked "is this card one of those" in several
dialects of its own: a spell filter for restricted mana (``SpellFilter``), a
type set and a "wanted" string for tapping or sacrificing another permanent,
and the string language of counting abilities. A `Selector` says all of it
in one form, and `compile_selector` turns one into a function once, so the
engine never interprets it while it plays.

Every field narrows: a card matches when each field that is set holds, or
when one of the ``alternatives`` matches.

A card's colours are read two ways today, and both stay until PR 17: a spell
by its printed colours when it has types (``printed_colors``), a permanent
by its colours or else its cost's.
"""

from dataclasses import dataclass, field

from simulation.manacost import COLORS

ROCK = "rock"
CREATURE = "creature"
ARTIFACT = "artifact"
#: In ``types``: any permanent at all.
PERMANENT = "permanent"
#: In ``types``: no card at all. An empty type set asked for nothing when a
#: permanent was tapped for another's mana, and still does.
NOTHING = "nothing"


@dataclass(frozen=True)
class Selector:
    """What a card must be.

    Attributes:
        types: Card types, any one of them; ``permanent`` is any. A card
            without types (a hand-written fixture card) has its kind's.
        noncreature: Not a creature.
        subtypes: Any one of these among the card's own subtypes (``subtypes``,
            the land types the engine reads).
        named_types: Any one of these among its printed subtypes and creature
            types: "Dragon spells".
        creature_types: Any one of these among its creature types.
        legendary, basic, defender: The card is so.
        color: The card has this colour.
        only_color: The card has this colour and no other.
        colorless: The card has no colour.
        multicolored: The card has two or more.
        cost_color: Its mana cost asks for this colour.
        land_type: Its engine land type is this (the reference deck's
            basic Swamps, written before land subtypes existed).
        names: Its name is one of these.
        printed_colors: Read a spell's colours as printed when it has types,
            not from its cost (restricted mana).
        printed_types: Read the card's types as printed, never its kind's:
            an enchantment counted for mana.
        alternatives: Or any one of these.
    """

    types: frozenset[str] = field(default_factory=frozenset)
    noncreature: bool = False
    subtypes: frozenset[str] = field(default_factory=frozenset)
    named_types: frozenset[str] = field(default_factory=frozenset)
    creature_types: frozenset[str] = field(default_factory=frozenset)
    legendary: bool = False
    basic: bool = False
    defender: bool = False
    color: str = ""
    only_color: str = ""
    colorless: bool = False
    multicolored: bool = False
    cost_color: str = ""
    land_type: str = ""
    names: frozenset[str] = field(default_factory=frozenset)
    printed_colors: bool = False
    printed_types: bool = False
    alternatives: tuple["Selector", ...] = ()


#: Everything: the selector that sets nothing.
ANY = Selector()


def types_of(card) -> frozenset[str]:
    """A card's types; a hand-written fixture card has only a kind."""
    return card.types or frozenset({ARTIFACT if card.kind == ROCK else card.kind})


def colors_of(card, printed: bool = False) -> frozenset[str]:
    """A card's colours: as printed, or else its cost's.

    ``printed``: a card with types has its printed colours even when there
    are none - a colourless artifact with a coloured activation is no
    coloured spell.
    """
    if printed:
        return frozenset(card.colors if (card.colors or card.types) else card.mana_cost.colors)
    return frozenset(card.colors or card.mana_cost.colors) & frozenset(COLORS)


def compile_selector(selector: Selector):
    """``selector`` as a function of one card, built once."""
    tests = []
    if selector.types and PERMANENT not in selector.types:
        wanted_types = selector.types
        if selector.printed_types:
            tests.append(lambda card: bool(card.types & wanted_types))
        else:
            tests.append(lambda card: bool(types_of(card) & wanted_types))
    if selector.noncreature:
        tests.append(lambda card: CREATURE not in types_of(card))
    if selector.subtypes:
        subtypes = selector.subtypes
        tests.append(lambda card: bool(card.subtypes & subtypes))
    if selector.named_types:
        named = selector.named_types
        tests.append(lambda card: bool(named & (card.printed_subtypes | card.creature_types)))
    if selector.creature_types:
        creature_types = selector.creature_types
        tests.append(lambda card: bool(card.creature_types & creature_types))
    if selector.legendary:
        tests.append(lambda card: card.legendary)
    if selector.basic:
        tests.append(lambda card: card.basic)
    if selector.defender:
        tests.append(lambda card: card.defender)
    printed = selector.printed_colors
    if selector.color:
        color = selector.color
        tests.append(lambda card: color in colors_of(card, printed))
    if selector.only_color:
        only = selector.only_color
        tests.append(lambda card: set(colors_of(card, printed)) == {only})
    if selector.colorless:
        tests.append(lambda card: not colors_of(card, printed))
    if selector.multicolored:
        tests.append(lambda card: len(colors_of(card, printed)) >= 2)
    if selector.cost_color:
        cost_color = selector.cost_color
        tests.append(lambda card: cost_color in card.mana_cost.colors)
    if selector.land_type:
        land_type = selector.land_type
        tests.append(lambda card: card.land_type == land_type)
    if selector.names:
        names = selector.names
        tests.append(lambda card: card.name in names)
    others = tuple(compile_selector(each) for each in selector.alternatives)
    if others:
        return lambda card: all(test(card) for test in tests) or any(
            other(card) for other in others)
    if not tests:
        return lambda card: True
    if len(tests) == 1:
        return tests[0]
    return lambda card: all(test(card) for test in tests)


_COMPILED: dict[Selector, object] = {}


def selects(selector: Selector):
    """The compiled form of ``selector``, compiled the first time it is asked."""
    found = _COMPILED.get(selector)
    if found is None:
        found = _COMPILED[selector] = compile_selector(selector)
    return found


# --- the old dialects, as selectors ------------------------------------------------


def wanted(types: frozenset[str], wanted_: str) -> Selector:
    """A type set and a "wanted" string - ``legendary``, a colour letter, a
    creature type or "" - as one selector: what tapping or sacrificing
    another permanent asks for."""
    types = frozenset(types) or frozenset({NOTHING})
    if not wanted_:
        return Selector(types=types)
    if wanted_ == "legendary":
        return Selector(types=types, legendary=True)
    if wanted_ in COLORS:
        return Selector(types=types, color=wanted_)
    return Selector(types=types, creature_types=frozenset({wanted_}))


def wanted_of(selector: Selector) -> tuple[frozenset[str], str]:
    """`wanted` backwards: the type set and the "wanted" string."""
    types = frozenset() if selector.types == {NOTHING} else selector.types
    if selector.legendary:
        return types, "legendary"
    if selector.color:
        return types, selector.color
    if selector.creature_types:
        return types, next(iter(selector.creature_types))
    return types, ""
