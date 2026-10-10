"""The deck page's cards: the grid, its filters, and the category bars (Phase 9 D).

Everything here is read off `adapter.readings`, the same object the engine
plays, so the deck page and the simulation report cannot sort a card
differently. A card's **types** are printed (`Card.types`, front face only);
its **categories** are `Card.categories` - the community roles unless the
owner replaced them - which is exactly what the report's "What you drew"
counts.

The commander is not in the grid or the counts. It is shown on its own at the
top of the page, and a deck list counts the ninety-nine.

The filters are plain query parameters (`?type=creature&cat=ramp&q=bolt`):
the grid works as an ordinary GET form, and htmx only saves the reload. The
search runs in Python over cards already loaded, so a query is never SQL; an
unknown type or category is ignored rather than refused, because a stale
bookmark is not an attack.
"""

from dataclasses import dataclass

from django.utils.translation import gettext

from decks.analysis import KARSTEN_MAX, KARSTEN_MIN
from simulation.cards import CARD_TYPES
from simulations import gaps as gaps_module
from simulations.engine.adapter import Reading
from simulations.report import CARD_TYPE_NAMES, SEEN_ROLES

#: The longest search the grid reads. A card's name and text are rarely longer
#: than this and nothing useful searches for more.
QUERY_MAX = 100

#: What the page calls each printed type, in English (translated where shown).
TYPE_LABELS = tuple((kind, CARD_TYPE_NAMES[kind]) for kind in CARD_TYPES)

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
    def assumptions(self) -> list[str]:
        """What the engine assumes to play this card, in words (P19 R16).

        Stated beside the card rather than only counted: a number that rests
        on "one opponent a round does not pay" is worth only as much as that
        assumption is at the reader's table.
        """
        return [gap.text for gap in self.reading.gaps
                if gap.field in gaps_module.ASSUMPTION_FIELDS]

    def matches(self, *, kind: str, category: str, query: str) -> bool:
        if kind and kind not in self.types:
            return False
        if category and category not in self.categories:
            return False
        if query:
            card = self.reading.oracle_card
            text = f"{card.name}\n{card.oracle_text or ''}".casefold()
            return query.casefold() in text
        return True


@dataclass(frozen=True)
class Filters:
    """What the grid is showing, cleaned."""

    kind: str = ""
    category: str = ""
    query: str = ""

    @classmethod
    def from_request(cls, params) -> "Filters":
        kind = params.get("type", "")
        category = params.get("cat", "")
        return cls(
            kind=kind if kind in dict(TYPE_LABELS) else "",
            category=category if category in dict(SEEN_ROLES) else "",
            query=" ".join(params.get("q", "").split())[:QUERY_MAX],
        )

    @property
    def active(self) -> bool:
        return bool(self.kind or self.category or self.query)


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


def cards(readings, questions) -> list[GridCard]:
    """The deck's cards, commander left out, the ones that need you first."""
    answered = {question.oracle_card.pk for question in questions if question.answered}
    open_ids = {question.oracle_card.pk for question in questions if not question.answered}
    return sorted(
        (
            GridCard(reading=reading,
                     open=reading.oracle_card.pk in open_ids,
                     answered=reading.oracle_card.pk in answered)
            for reading in readings if not reading.is_commander
        ),
        key=lambda card: (not card.open, card.name.lower()),
    )


def shown(grid: list[GridCard], filters: Filters) -> list[GridCard]:
    return [card for card in grid
            if card.matches(kind=filters.kind, category=filters.category,
                            query=filters.query)]


def bars(grid: list[GridCard]) -> tuple[list[Bar], list[Bar]]:
    """Count per printed type and per category, on one scale for both lists.

    A type nobody plays is left out (a deck without battles has no battle
    row); a category is always shown, because "0 removal" is the answer.
    """
    counts: dict[str, int] = {}
    for card in grid:
        for kind in card.types:
            counts[f"type:{kind}"] = counts.get(f"type:{kind}", 0) + card.quantity
        for category in card.categories:
            counts[f"role:{category}"] = counts.get(f"role:{category}", 0) + card.quantity

    rows = [("type", kind, label) for kind, label in TYPE_LABELS
            if counts.get(f"type:{kind}")]
    rows += [("cat", category, label) for category, label in SEEN_ROLES]
    scale = max([counts.get(_key(param, value), 0) for param, value, _ in rows]
                + [high for _, high in TEMPLATE.values()] + [1])

    made = [_bar(param, value, label, counts.get(_key(param, value), 0), scale)
            for param, value, label in rows]
    return ([bar for bar in made if bar.param == "type"],
            [bar for bar in made if bar.param == "cat"])


def _key(param: str, value: str) -> str:
    return f"type:{value}" if param == "type" else f"role:{value}"


def _bar(param: str, value: str, label: str, count: int, scale: int) -> Bar:
    key = _key(param, value)
    template = TEMPLATE.get(key)
    band = None
    band_label = ""
    if template is not None:
        low, high = template
        band = (round(100 * low / scale, 1), round(100 * (high - low) / scale, 1))
        band_label = f"{low}" if low == high else f"{low}-{high}"
    return Bar(key=key, label=gettext(label), count=count, param=param, value=value,
               width=round(100 * count / scale, 1), band=band, band_label=band_label)


__all__ = ["Bar", "Filters", "GridCard", "QUERY_MAX", "TEMPLATE", "TYPE_LABELS",
           "bars", "cards", "shown"]
