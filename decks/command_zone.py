"""Who an import puts in the command zone, and why nobody when nobody (P20 F2).

The file's own marker decides - an Archidekt tag, a `Commander` heading, a
`*CMDR*` - and only for cards that may lead a deck (`cards.commanders`). One
marked card that stands alone, or two that may share the zone. Anything else
sets nobody and records why, because the three reasons used to read as one:
"the file did not mark one", also under a file that had named two partners.

Where the file marks nobody, its **order** may still say it (decision E4):
Moxfield's text export puts the commanders on the first lines, its MTGO
export in a last block after a blank line. That is kept as a suggestion the
deck page fills in, never as a commander - picking "the only legendary
creature" would be right often enough to be trusted and wrong often enough to
hurt.
"""

from dataclasses import dataclass, field

from cards import commanders
from cards.names import normalise
from decks.models import CommanderIssue


@dataclass
class Reading:
    """The command zone an import found: up to two cards, or why none."""

    cards: list = field(default_factory=list)
    issue: str = ""
    #: The names the file marked, as written.
    named: list[str] = field(default_factory=list)


def ordered(first, second) -> list:
    """A pair with the commander that leads first: a Background goes second."""
    if commanders.is_background(first) and not commanders.is_background(second):
        return [second, first]
    return [first, second]


def read(report) -> Reading:
    """The cards `report` marked as commander, if they may lead together."""
    marked = [resolution for resolution in report.resolutions if resolution.row.is_commander]
    named = list(dict.fromkeys(resolution.row.name for resolution in marked))
    if not marked:
        return Reading(issue=CommanderIssue.UNMARKED)
    if any(not resolution.resolved for resolution in marked):
        return Reading(issue=CommanderIssue.NOT_FOUND, named=named)

    cards = list({resolution.card.pk: resolution.card for resolution in marked}.values())
    if len(cards) == 1:
        if commanders.stands_alone(cards[0]):
            return Reading(cards=cards, named=named)
        return Reading(issue=CommanderIssue.NOT_ALLOWED, named=named)
    if len(cards) == 2 and commanders.pair_ok(*cards):
        return Reading(cards=ordered(*cards), named=named)
    if len(cards) == 2 and not all(commanders.can_be_commander(card) for card in cards):
        return Reading(issue=CommanderIssue.NOT_ALLOWED, named=named)
    return Reading(issue=CommanderIssue.NOT_PAIR, named=named)


def _valid(cards) -> bool:
    if len(cards) == 1:
        return commanders.stands_alone(cards[0])
    return len(cards) == 2 and commanders.pair_ok(*cards)


def _before(earlier, later) -> bool:
    """`earlier` sorts before `later`, by name as a person reads it."""
    return normalise(earlier.name) < normalise(later.name)


def suggest(report) -> list:
    """The commander(s) the file's order points at, or `[]`.

    A **last block** of one or two cards after a gap in the line numbers (a
    blank line, MTGO), else the **first** one or two lines when they break
    the list's alphabetical order (Moxfield puts the commanders first and
    sorts the rest). An alphabetical file (Archidekt) says nothing by its
    order, and gets no suggestion.
    """
    found = [resolution for resolution in report.resolutions if resolution.resolved]
    if len(found) < 3:
        return []

    for size in (1, 2):
        block, before = found[-size:], found[-size - 1]
        gap = block[0].row.line_number - before.row.line_number > 1
        inside = all(b.row.line_number - a.row.line_number == 1
                     for a, b in zip(block, block[1:], strict=False))
        if gap and inside:
            cards = [resolution.card for resolution in block]
            if _valid(cards):
                return ordered(*cards) if size == 2 else cards

    for size in (2, 1):
        head, after = found[:size], found[size]
        cards = [resolution.card for resolution in head]
        if _valid(cards) and _before(after.card, cards[-1]):
            return ordered(*cards) if size == 2 else cards
    return []


# --- choosing by hand ------------------------------------------------------


@dataclass
class Second:
    """A card that may be the second commander, and the leads it pairs with."""

    card: object
    leads: list[str]


@dataclass
class Choice:
    """What the deck page offers as the commander(s) of `deck`.

    `leads` may be the commander on their own; `seconds` may join one of
    them, each with the leads it pairs with (the page shows a second only
    beside those). `chosen` is what the fields start on: the commanders the
    deck has, else what the last import suggested, else nothing - never the
    first card of the list (Lauf 1 saved Bayou that way).
    """

    leads: list = field(default_factory=list)
    seconds: list[Second] = field(default_factory=list)
    chosen: list = field(default_factory=list)
    #: `chosen` came from the file's order, not from the deck.
    suggested: bool = False

    def __bool__(self) -> bool:
        return bool(self.leads)

    @property
    def lead_id(self) -> str:
        return str(self.chosen[0].pk) if self.chosen else ""

    @property
    def second_id(self) -> str:
        return str(self.chosen[1].pk) if len(self.chosen) > 1 else ""

    @property
    def names(self) -> str:
        """`chosen` as one line, `Krark + Thrasios`."""
        return " + ".join(card.front_name for card in self.chosen)


def pool(deck) -> list:
    """Every card `deck` holds, command zone first, each once."""
    cards = {card.pk: card for card in deck.commanders}
    for entry in deck.entries.all():
        cards.setdefault(entry.oracle_card_id, entry.oracle_card)
    return list(cards.values())


def _by_name(cards) -> list:
    return sorted(cards, key=lambda card: normalise(card.name))


def choice(deck, suggested_ids=()) -> Choice:
    """The commander choice for `deck` (see `Choice`)."""
    cards = pool(deck)
    leads = _by_name(card for card in cards if commanders.stands_alone(card))
    seconds = []
    for card in _by_name(card for card in cards if commanders.can_pair(card)):
        pairs = [str(lead.pk) for lead in leads if commanders.pair_ok(lead, card)]
        if pairs:
            seconds.append(Second(card=card, leads=pairs))

    if deck.commanders:
        return Choice(leads, seconds, chosen=deck.commanders)
    by_id = {str(card.pk): card for card in cards}
    hinted = [by_id[pk] for pk in suggested_ids if pk in by_id]
    if hinted and _valid(hinted):
        return Choice(leads, seconds, chosen=hinted, suggested=True)
    return Choice(leads, seconds)


__all__ = ["Choice", "Reading", "Second", "choice", "ordered", "pool", "read", "suggest"]
