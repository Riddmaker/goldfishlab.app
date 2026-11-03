"""Which cards of a deck work together: pairs and engines (Themen-Doc, Paket 1, PR 3).

Both are read from the themes a deck was found to be built around
(`cards.themes.detect`), never from the whole list: a stray Blood Artist in
a deck with no sacrifice plan pairs with nothing.

* A **pair** is two cards a theme's rule joins: the first serves the
  second ("Viscera Seer sacrifices, Blood Artist pays off each death").
  Its strength is the product of three terms, each shown under "Advanced":
  the rule's weight, how rare the tags are that put each card in its piece
  (a rare tag is a stronger sign than a broad one), and the theme's fill in
  this deck (its density).
* An **engine** is one card for each piece of a theme, two to four cards
  that together make value again and again: at least one of them must
  repeat (`ThemeCard.repeats`). It is as strong as the theme behind it, so
  it carries the theme's level. A theme with an empty piece gives an
  engine that names what is missing instead.

Typal has neither: a lord and one of its creatures is a pair of every
deck of that type, hundreds of times over, and says nothing new.

Nothing here touches the database; `decks.themes.deck_reading` builds the
cards and the tag sizes.
"""

import math
from dataclasses import dataclass

from django.utils.translation import gettext, pgettext

from cards import themes

#: The rarity of a card that is in a piece by its type line or by our own
#: mechanics rather than by a tag: a middling sign, neither rare nor broad.
SPEC_PLAIN = 0.5
#: The lowest rarity a tag gets: a tag on every card still says something.
SPEC_FLOOR = 0.1
#: A tag on this few cards or fewer counts as fully rare.
SPEC_RARE = 20


def spec(size: int, total: int) -> float:
    """How rare a tag is, 0.1 to 1: the inverse document frequency of a tag
    on ``size`` of the ``total`` cards, scaled so a tag on `SPEC_RARE`
    cards is 1."""
    if size <= 0 or total <= SPEC_RARE:
        return 1.0
    value = math.log(total / size) / math.log(total / SPEC_RARE)
    return round(min(max(value, SPEC_FLOOR), 1.0), 2)


def card_spec(card: themes.ThemeCard, piece: themes.Piece, sizes: dict[str, int],
              total: int) -> float:
    """The rarest tag that puts the card in the piece, or `SPEC_PLAIN` when
    no tag does."""
    matched = (card.tags & piece.tags) | (card.direct & piece.direct)
    if not matched:
        return SPEC_PLAIN
    return max(spec(sizes.get(slug, 0), total) for slug in matched)


def _say(steps) -> str:
    """Clauses joined into one sentence, in the active language."""
    clauses = [gettext(does) % {"card": name} for name, does in steps]
    return pgettext("joins clauses", ", ").join(clauses) + pgettext("ends a sentence", ".")


@dataclass(frozen=True)
class Pair:
    theme: str
    first: str
    second: str
    #: The clauses of the two pieces (msgids), first then second.
    does: tuple[str, str]
    weight: float
    spec_first: float
    spec_second: float
    density: float

    @property
    def score(self) -> float:
        return round(self.weight * self.spec_first * self.spec_second * self.density, 3)

    @property
    def sentence(self) -> str:
        return _say(((self.first, self.does[0]), (self.second, self.does[1])))


@dataclass(frozen=True)
class Engine:
    theme: str
    label: str
    level: str
    #: (card name, clause msgid) in the order the theme's rules say them.
    steps: tuple[tuple[str, str], ...]
    #: Labels of the pieces no card fills (msgids).
    missing: tuple[str, ...] = ()
    #: The cards that work again and again.
    repeating: tuple[str, ...] = ()

    @property
    def cards(self) -> tuple[str, ...]:
        return tuple(name for name, _does in self.steps)

    @property
    def pieces(self) -> int:
        """Every piece of its theme, filled or not."""
        return len(self.steps) + len(self.missing)

    @property
    def sentence(self) -> str:
        return _say(self.steps)


def _holders(piece: themes.Piece, cards) -> list[themes.ThemeCard]:
    return [card for card in cards if card.weight and piece.holds(card)]


def pairs(found, cards, sizes: dict[str, int], total: int) -> list[Pair]:
    """Every pair the deck's themes join, each pair of cards once (at its
    strongest), the strongest first and varied (`_varied`)."""
    cards = list(cards)
    best: dict[frozenset, Pair] = {}
    for result in found:
        theme = themes.BY_KEY.get(result.theme)
        if theme is None:
            continue
        for first_key, second_key, weight in theme.rules:
            first, second = theme.piece(first_key), theme.piece(second_key)
            for a in _holders(first, cards):
                for b in _holders(second, cards):
                    if a.name == b.name:
                        continue
                    pair = Pair(theme.key, a.name, b.name, (first.does, second.does), weight,
                                card_spec(a, first, sizes, total),
                                card_spec(b, second, sizes, total), result.fill)
                    key = frozenset((a.name, b.name))
                    if key not in best or pair.score > best[key].score:
                        best[key] = pair
    ranked = sorted(best.values(), key=lambda pair: (-pair.score, pair.first, pair.second))
    return _varied(ranked)


def _varied(ranked: list[Pair]) -> list[Pair]:
    """The pairs in rounds, strongest first in each: a round takes only pairs
    whose cards no earlier pair of it named. One payoff with twenty sources
    would fill the first five rows alone; this way they show five plans."""
    order, left = [], ranked
    while left:
        seen, later = set(), []
        for pair in left:
            if seen.isdisjoint((pair.first, pair.second)):
                order.append(pair)
                seen.update((pair.first, pair.second))
            else:
                later.append(pair)
        left = later
    return order


def engines(found, cards, sizes: dict[str, int], total: int) -> list[Engine]:
    """One engine for each theme found, the strongest theme first.

    Each piece takes its best card not already taken: the commander first
    (it is there every game), then a card that repeats, then the rarest
    tag. A complete engine with no card that repeats is no engine and is
    left out; an incomplete one is kept, as what the deck could still add.
    """
    cards = list(cards)
    made = []
    for result in found:
        theme = themes.BY_KEY.get(result.theme)
        if theme is None:
            continue
        steps, missing, taken = [], [], set()
        for piece in theme.flow:
            options = [card for card in _holders(piece, cards) if card.name not in taken]
            if not options:
                missing.append(piece.label)
                continue
            card = min(options, key=lambda card, piece=piece: (
                not card.commander, not card.repeats,
                -card_spec(card, piece, sizes, total), card.name))
            taken.add(card.name)
            steps.append((card.name, piece.does))
        repeating = tuple(card.name for card in cards if card.name in taken and card.repeats)
        if not steps or (not missing and not repeating):
            continue
        made.append(Engine(theme.key, theme.label, result.level, tuple(steps), tuple(missing),
                           repeating))
    return made
