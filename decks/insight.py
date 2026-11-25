"""More about this card (B-22): what one card is strong with, in this deck.

Read from what the deck page already found, nothing new is guessed here:

* **partners** are the cards of this deck the card works with - its pairs
  and engines first (`cards.synergy`, strongest first), then the cards that
  fill another piece of a theme it is part of;
* **kinds** are those other pieces by name ("Token makers"), the kinds of
  card it is strong with, with how many this deck holds - none is a real
  answer and the most useful one.

The combos come from `combos.services.card_combos`; the page puts both side
by side (`templates/simulations/_insight.html`).
"""

from dataclasses import dataclass

from cards import synergy

#: The partners shown; past this the list stops being a hint.
PARTNERS_MAX = 8
#: The cards named under one kind.
KIND_CARDS_MAX = 5

PAIR = synergy.PAIR
ENGINE = synergy.ENGINE
THEME = "theme"


@dataclass(frozen=True)
class Partner:
    name: str
    #: PAIR, ENGINE or THEME: how the two are known to work together.
    kind: str
    #: The pair's or engine's sentence; for THEME the piece's label (a msgid).
    why: str
    #: For THEME, the theme's label (a msgid) and subject ("Angel").
    theme: str = ""
    subject: str = ""


@dataclass(frozen=True)
class Kind:
    #: The piece's label and its theme's (msgids), the theme's subject.
    label: str
    theme: str
    subject: str
    #: How many cards of this deck fill it (each card once: the theme's own
    #: count weighs copies and the commander), and the first few by name.
    count: int
    cards: tuple[str, ...]

    @property
    def more(self) -> bool:
        return self.count > len(self.cards)


@dataclass(frozen=True)
class StrongWith:
    partners: tuple[Partner, ...] = ()
    kinds: tuple[Kind, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.partners or self.kinds)

    @property
    def worked(self) -> tuple[Partner, ...]:
        """The partners of a pair or an engine, each with its sentence; the
        others are named under their kind."""
        return tuple(partner for partner in self.partners if partner.kind != THEME)


def strong_with(reading, name: str) -> StrongWith:
    """What `name` (a card's whole name, as the reading has it) works with."""
    partners: dict[str, Partner] = {}

    def add(partner: Partner) -> None:
        if partner.name != name and partner.name not in partners:
            partners[partner.name] = partner

    for pair in (*reading.pairs, *reading.more_pairs):
        if name in pair.cards and not pair.gone:
            for other in pair.cards:
                add(Partner(name=other, kind=PAIR, why=pair.sentence))
    for engine in reading.engines:
        if name in engine.cards and not engine.gone:
            for other in engine.cards:
                add(Partner(name=other, kind=ENGINE, why=engine.sentence))

    kinds: list[Kind] = []
    for found in reading.themes:
        if not any(name in part.cards for part in found.parts):
            continue
        for part in found.parts:
            if name in part.cards:
                continue
            kinds.append(Kind(label=part.label, theme=found.label, subject=found.subject,
                              count=len(part.cards), cards=part.cards[:KIND_CARDS_MAX]))
            for other in part.cards:
                add(Partner(name=other, kind=THEME, why=part.label, theme=found.label,
                            subject=found.subject))

    return StrongWith(partners=tuple(partners.values())[:PARTNERS_MAX], kinds=tuple(kinds))
