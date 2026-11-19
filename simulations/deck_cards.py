"""The deck page's cards: the grid, its filters, and the category bars (Phase 9 D).

Everything here is read off `adapter.readings`, the same object the engine
plays, so the deck page and the simulation report cannot sort a card
differently. A card's **types** are printed (`Card.types`, front face only);
its **categories** are `Card.categories` - derived from its blocks and the
community tags unless the owner replaced them - which is exactly what the
report's "What you drew" counts.

A category bar shows two numbers (card model phase 3): every card in the
category, which is what the template counts, and the cards whose **main**
role it is (`Reading.primary`). A card counts in as many categories as it
fits but has one main role at most, so the main roles and the cards with
none add up to the deck.

The commander is not in the grid or the counts. It is shown on its own at the
top of the page, and a deck list counts the ninety-nine.

The filters are plain query parameters (`?type=creature&cat=ramp&q=bolt`):
the grid works as an ordinary GET form, and htmx only saves the reload. The
search runs in Python over cards already loaded, so a query is never SQL; an
unknown type or category is ignored rather than refused, because a stale
bookmark is not an attack.

Since deck page v2, PR F a chip can be chosen together with others
(`?cat=ramp&cat=draw`): the grid shows every card that wears any chosen chip,
grouped under each (`groups`), and the card explorer draws by which turn one
of them is seen (static/js/card-explorer.js, from `explorer_cards`).
"""

from dataclasses import dataclass

from django.utils.translation import gettext

from core import symbols
from decks.analysis import KARSTEN_MAX, KARSTEN_MIN
from simulation.cards import CARD_TYPES
from simulations import gaps as gaps_module
from simulations.engine.adapter import Reading
from simulations.report import CARD_TYPE_NAMES, SEEN_MECHANIC_ROLES, SEEN_ROLES, SEEN_STRATEGY_ROLES

#: The longest search the grid reads. A card's name and text are rarely longer
#: than this and nothing useful searches for more.
QUERY_MAX = 100

#: What the page calls each printed type, in English (translated where shown).
TYPE_LABELS = tuple((kind, CARD_TYPE_NAMES[kind]) for kind in CARD_TYPES)
#: The roles a theme is built from (sacrifice outlet, treasure, ...): chips of
#: their own, shown when the deck has a card of one.
THEME_ROLES = SEEN_STRATEGY_ROLES + SEEN_MECHANIC_ROLES
#: Every category a chip can stand for.
CATEGORY_LABELS = dict(SEEN_ROLES + THEME_ROLES)

#: A common template for a 100-card Commander deck, shown as a faint band
#: behind the bar and labelled as such - never as a verdict. From the Command
#: Zone deckbuilding template (Phase 9, "The category vocabulary"); the lands
#: are Frank Karsten's band, the one the land tile judges by. A single number is a
#: band of width zero, drawn as a tick.
TEMPLATE = {
    "type:land": (KARSTEN_MIN, KARSTEN_MAX),
    "role:ramp": (10, 10),
    "role:draw": (10, 10),
    "role:removal": (8, 10),
    "role:wipe": (2, 4),
}


@dataclass(frozen=True)
class GridCard:
    """One card of the grid."""

    reading: Reading
    open: bool
    answered: bool
    #: Open because a correction of the owner's no longer fits its reading.
    to_check: bool = False
    #: The printings the imported file named (`decks.models.Printings`, P20 F1).
    printing: object = None

    @property
    def oracle_card(self):
        return self.reading.oracle_card

    @property
    def name(self) -> str:
        return self.reading.oracle_card.front_name

    @property
    def quantity(self) -> int:
        return self.reading.quantity

    @property
    def types(self) -> frozenset[str]:
        return self.reading.card.types

    @property
    def categories(self) -> frozenset[str]:
        return self.reading.card.categories

    @property
    def primary(self) -> str:
        return self.reading.primary

    @property
    def assumptions(self) -> list[str]:
        """What the engine assumes to play this card, in words (P19 R16).

        Stated beside the card rather than only counted: a number that rests
        on "one opponent a round does not pay" is worth only as much as that
        assumption is at the reader's table.
        """
        return [gap.text for gap in self.reading.gaps
                if gap.field in gaps_module.ASSUMPTION_FIELDS]

    def matches(self, *, kinds=(), categories=(), query: str = "") -> bool:
        """Any chosen chip (a card shows under each it wears), and the search."""
        if (kinds or categories) and not (self.types & set(kinds)
                                          or self.categories & set(categories)):
            return False
        if query:
            card = self.reading.oracle_card
            text = f"{card.name}\n{card.oracle_text or ''}".casefold()
            return query.casefold() in text
        return True


@dataclass(frozen=True)
class Filters:
    """What the grid is showing, cleaned."""

    kinds: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    query: str = ""

    @classmethod
    def from_request(cls, params) -> "Filters":
        """Known chips only, each once, in the page's order."""
        kinds = set(params.getlist("type"))
        categories = set(params.getlist("cat"))
        return cls(
            kinds=tuple(kind for kind, _ in TYPE_LABELS if kind in kinds),
            categories=tuple(key for key in CATEGORY_LABELS if key in categories),
            query=" ".join(params.get("q", "").split())[:QUERY_MAX],
        )

    @property
    def active(self) -> bool:
        return bool(self.kinds or self.categories or self.query)

    @property
    def chosen(self) -> tuple[str, ...]:
        """The chosen chips as the page names them: "type:creature", "role:ramp"."""
        return (tuple(_key("type", kind) for kind in self.kinds)
                + tuple(_key("cat", key) for key in self.categories))

    def query_items(self) -> list[tuple[str, str]]:
        """The filters as query parameters, for a link back to them."""
        return ([("type", kind) for kind in self.kinds]
                + [("cat", key) for key in self.categories]
                + ([("q", self.query)] if self.query else []))


#: The categories with a symbol of their own (core/icons/), Paket 4, PR 15.
@dataclass(frozen=True)
class Bar:
    """One row of the category bars, in percent of the page's shared scale."""

    key: str
    label: str
    count: int
    param: str
    value: str
    width: float
    band: tuple[float, float] | None
    band_label: str
    #: Of a category: the cards whose main role it is. `None` for a type, and
    #: for every category while no card has a main role yet (a profile not
    #: derived again since card model phase 3).
    slots: int | None = None

    @property
    def icon(self) -> str:
        """The symbol beside the label, or "" (DESIGN.md, SYMBOLS): every role
        and every type has one since deck page v2, PR C (`core.symbols`)."""
        return symbols.for_role(self.value) if self.param == "cat" else symbols.for_type(self.value)


def cards(readings, questions, printings: dict | None = None) -> list[GridCard]:
    """The deck's cards, commander left out, the ones that need you first.

    `printings` maps a card's id to what the imported file said about its
    printing, shown under the name.
    """
    printings = printings or {}
    answered = {question.oracle_card.pk for question in questions if question.answered}
    open_ids = {question.oracle_card.pk for question in questions if not question.answered}
    return sorted(
        (
            GridCard(reading=reading,
                     open=reading.oracle_card.pk in open_ids,
                     answered=reading.oracle_card.pk in answered,
                     to_check=bool(reading.checks),
                     printing=printings.get(reading.oracle_card.pk))
            for reading in readings if not reading.is_commander
        ),
        key=lambda card: (not card.open, card.name.lower()),
    )


def shown(grid: list[GridCard], filters: Filters) -> list[GridCard]:
    return [card for card in grid
            if card.matches(kinds=filters.kinds, categories=filters.categories,
                            query=filters.query)]


@dataclass(frozen=True)
class Group:
    """The cards of one chosen chip, as the grid shows them."""

    key: str
    label: str
    icon: str
    cards: list

    @property
    def count(self) -> int:
        return sum(card.quantity for card in self.cards)


def groups(shown_cards: list[GridCard], filters: Filters) -> list[Group]:
    """The shown cards under each chosen chip, in the chips' order; a card
    under every chip it wears. Empty while no chip is chosen."""
    made = [Group(key=_key("type", kind), label=gettext(dict(TYPE_LABELS)[kind]),
                  icon=symbols.for_type(kind),
                  cards=[card for card in shown_cards if kind in card.types])
            for kind in filters.kinds]
    made += [Group(key=_key("cat", key), label=gettext(CATEGORY_LABELS[key]),
                   icon=symbols.for_role(key),
                   cards=[card for card in shown_cards if key in card.categories])
             for key in filters.categories]
    return made


def theme_chips(grid: list[GridCard]) -> list[tuple[str, str, str]]:
    """(value, label, icon) of each theme role a card of the deck has."""
    worn = {category for card in grid for category in card.categories}
    return [(key, gettext(label), symbols.for_role(key))
            for key, label in THEME_ROLES if key in worn]


def explorer_cards(grid: list[GridCard]) -> list[dict]:
    """The library for the card explorer's curve, as the page's JSON gives
    it: each card's copies and the chips it wears, nothing else. The
    commander is not in the grid, so it is not in the library either."""
    return [{"n": card.name, "q": card.quantity,
             "k": sorted([_key("type", kind) for kind in card.types]
                         + [_key("cat", key) for key in card.categories
                            if key in CATEGORY_LABELS])}
            for card in grid]


def bars(grid: list[GridCard]) -> tuple[list[Bar], list[Bar]]:
    """Count per printed type and per category, on one scale for both lists.

    A type nobody plays is left out (a deck without battles has no battle
    row); a category is always shown, because "0 removal" is the answer.
    """
    counts: dict[str, int] = {}
    slots: dict[str, int] = {}
    for card in grid:
        for kind in card.types:
            counts[f"type:{kind}"] = counts.get(f"type:{kind}", 0) + card.quantity
        for category in card.categories:
            counts[f"role:{category}"] = counts.get(f"role:{category}", 0) + card.quantity
        if card.primary:
            slots[card.primary] = slots.get(card.primary, 0) + card.quantity
    ranked = any(card.primary for card in grid)

    rows = [("type", kind, label) for kind, label in TYPE_LABELS
            if counts.get(f"type:{kind}")]
    rows += [("cat", category, label) for category, label in SEEN_ROLES]
    scale = max([counts.get(_key(param, value), 0) for param, value, _ in rows]
                + [high for _, high in TEMPLATE.values()] + [1])

    made = [_bar(param, value, label, counts.get(_key(param, value), 0), scale,
                 slots.get(value, 0) if param == "cat" and ranked else None)
            for param, value, label in rows]
    return ([bar for bar in made if bar.param == "type"],
            [bar for bar in made if bar.param == "cat"])


def _key(param: str, value: str) -> str:
    return f"type:{value}" if param == "type" else f"role:{value}"


def unranked(grid: list[GridCard]) -> int | None:
    """How many cards have no main role - lands, plan cards - or `None` while
    no card has one (see `Bar.slots`)."""
    if not any(card.primary for card in grid):
        return None
    return sum(card.quantity for card in grid if not card.primary)


def _bar(param: str, value: str, label: str, count: int, scale: int,
         slots: int | None = None) -> Bar:
    key = _key(param, value)
    template = TEMPLATE.get(key)
    band = None
    band_label = ""
    if template is not None:
        low, high = template
        band = (round(100 * low / scale, 1), round(100 * (high - low) / scale, 1))
        band_label = f"{low}" if low == high else f"{low}-{high}"
    return Bar(key=key, label=gettext(label), count=count, param=param, value=value,
               width=round(100 * count / scale, 1), band=band, band_label=band_label,
               slots=slots)


__all__ = ["Bar", "CATEGORY_LABELS", "Filters", "GridCard", "Group", "QUERY_MAX", "TEMPLATE",
           "THEME_ROLES", "TYPE_LABELS", "bars", "cards", "explorer_cards", "groups", "shown",
           "theme_chips", "unranked"]
