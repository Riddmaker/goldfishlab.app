"""The engine checked against Frank Karsten's coloured-source tables (P16).

Karsten's "How Many Sources Do You Need to Consistently Cast Your Spells? A
2022 Update" (TCGplayer, 2 August 2022) answers one question per mana cost:
how many sources of a colour a deck needs to cast a spell of that cost on
curve. His 99-card column is `PUBLISHED`. His assumptions, all taken over here:

* 99 cards, 41 lands, and no other mana sources or card selection;
* the multiplayer rules: the first mulligan is free and the starting player
  draws on turn 1;
* a spell of mana value M with N coloured pips is cast on curve when at least
  N sources of its colour have been **drawn** by turn M;
* conditioned on having drawn at least M lands by turn M - running out of
  lands is a land-count question, not a colour question;
* "consistently" means a (89 + M) % chance.

Two players answer it here:

* `engine_tally`: the engine's own shuffle, mulligan rule and draws
  (`Game.take_opening_hand`, `Game.draw`), exactly as in a report.
* `karsten_tally`: Karsten's London mulligan strategy, written from the article's
  description. It shows that the counting here is Karsten's: with his rule it
  lands on his numbers, so wherever the engine differs, the mulligan rule is
  the reason.

The engine never looks at a land's colour when it mulligans or bottoms, so all
41 lands are numbered and "S sources" means "the lands numbered below S". The
lands are exchangeable, which makes one game the answer for every S at once.
Karsten's rule bottoms off-colour lands first, so it has to play each S apart.
"""

import random
from collections import Counter
from dataclasses import dataclass

from simulation.cards import CREATURE, LAND, Card, DeckDefinition
from simulation.game import Game

SOURCE = ("https://www.tcgplayer.com/content/article/"
          "how-many-sources-do-you-need-to-consistently-cast-your-spells-a-2022-update/")

DECK_SIZE = 99
LANDS = 41
LAST_TURN = 7

#: Karsten's 2022 table, 99-card decks: sources needed per mana cost, where
#: "C" is a coloured pip and the digit the generic part. In his order.
PUBLISHED = {
    "5C": 14, "4C": 15, "3C": 16, "2C": 18, "5CC": 20, "1C": 19, "4CC": 22,
    "C": 19, "3CC": 23, "4CCC": 26, "2CC": 26, "3CCC": 28, "1CC": 28,
    "2CCC": 30, "CC": 30, "1CCC": 33, "1CCCC": 36, "CCC": 36, "CCCC": 39,
}

#: What the engine needs for the same costs: `engine_karsten`, 1,000,000
#: games, seed 2022 (22 October 2026, engine v37). The methodology page prints
#: it; tests/test_karsten.py and the command fail when it goes stale.
REQUIRED = {
    "5C": 14, "4C": 15, "3C": 17, "2C": 18, "5CC": 20, "1C": 20, "4CC": 22,
    "C": 21, "3CC": 24, "4CCC": 26, "2CC": 26, "3CCC": 28, "1CC": 29,
    "2CCC": 30, "CC": 32, "1CCC": 33, "1CCCC": 36, "CCC": 37, "CCCC": 39,
}

_LANDS = tuple(Card(f"Land {number}", 0, 0, 0, LAND, frozenset(), types=frozenset({"land"}))
               for number in range(LANDS))
_NUMBER = {land.name: number for number, land in enumerate(_LANDS)}
# Mana value 3: the engine bottoms the most expensive card first, and any
# spell is more expensive than a land.
_SPELL = Card("Spell", 3, 0, 3, CREATURE, frozenset(), types=frozenset({"creature"}))
DECK = DeckDefinition(name="Karsten", commander=None,
                      library=_LANDS + (_SPELL,) * (DECK_SIZE - LANDS))


@dataclass(frozen=True)
class Cost:
    """A mana cost of one colour: ``generic`` plus ``pips`` coloured symbols."""

    text: str
    generic: int
    pips: int

    @property
    def mv(self) -> int:
        return self.generic + self.pips

    @property
    def target(self) -> int:
        """Karsten's consistency target in percent: 89 plus the mana value."""
        return 89 + self.mv

    @classmethod
    def parse(cls, text: str) -> "Cost":
        pips = text.count("C")
        generic = text[:-pips] if pips else text
        return cls(text, int(generic or 0), pips)


COSTS = tuple(Cost.parse(text) for text in PUBLISHED)


class Tally:
    """For each turn, how many games drew enough lands, and for each pip
    count the number of the pip-th lowest land drawn in them."""

    def __init__(self):
        self.relevant = Counter()
        self.lowest = Counter()

    def add(self, turn: int, numbers: list[int]) -> None:
        """One game's lands by ``turn``, as their numbers, sorted."""
        if len(numbers) < turn:
            return
        self.relevant[turn] += 1
        for pips in range(1, min(turn, 4) + 1):
            self.lowest[turn, pips, numbers[pips - 1]] += 1

    def chance(self, cost: Cost, sources: int) -> float:
        """The chance to cast ``cost`` on curve with ``sources`` of its colour."""
        hits = sum(self.lowest[cost.mv, cost.pips, number] for number in range(sources))
        return hits / self.relevant[cost.mv]

    def required(self, cost: Cost) -> int | None:
        """The fewest sources that reach the target; None if even all lands
        do not."""
        for sources in range(cost.pips, LANDS + 1):
            if self.chance(cost, sources) * 100 >= cost.target:
                return sources
        return None


def engine_tally(games: int, seed: int = 2022) -> Tally:
    """``games`` games of the engine's own opening hand and draws."""
    rng = random.Random(seed)
    tally = Tally()
    for _ in range(games):
        game = Game(rng, on_the_play=False, deck=DECK)
        game.take_opening_hand()
        numbers = [_NUMBER[card.name] for card in game.drawn if card.is_land]
        for turn in range(1, LAST_TURN + 1):
            numbers.extend(_NUMBER[card.name] for card in game.draw(1) if card.is_land)
            tally.add(turn, sorted(numbers))
    return tally


# --- Karsten's mulligan strategy --------------------------------------------
# A card is its position in the deck: below `good` a land of the colour, below
# LANDS another land, else a spell. Only the top of the library is ever drawn,
# so a hand and its draws are one sample without replacement.

def _bottom(hand: list[int], good: int, lands: int, spells: int) -> list[int]:
    """Put ``lands`` lands (other colours first) and ``spells`` spells on the
    bottom of ``hand``."""
    colour = [card for card in hand if card < good]
    others = [card for card in hand if good <= card < LANDS]
    spell_cards = [card for card in hand if card >= LANDS]
    from_others = min(lands, len(others))
    from_colour = lands - from_others
    return colour[from_colour:] + others[from_others:] + spell_cards[spells:]


def _karsten_hand(rng: random.Random, good: int) -> tuple[list[int], list[int]]:
    """The hand Karsten keeps, and the top of the library after it."""
    for size in ("free", 7, 6, 5, 4):
        cards = rng.sample(range(DECK_SIZE), 7 + LAST_TURN)
        hand, top = cards[:7], cards[7:]
        spells = sum(1 for card in hand if card >= LANDS)
        if size == "free":
            if 3 <= 7 - spells <= 5:
                return hand, top
            continue
        if size == 7:
            if 2 <= 7 - spells <= 5:
                return hand, top
            continue
        # Bottoming aims at three lands: 6 cards = 3 + 3, 5 = 3 + 2, 4 = 3 + 1.
        bottom = 7 - size
        to_spells = min(bottom, max(spells - (size - 3), 0))
        hand = _bottom(hand, good, bottom - to_spells, to_spells)
        if size == 4 or 2 <= sum(1 for card in hand if card < LANDS) <= 4:
            return hand, top
    raise AssertionError("a four-card hand is always kept")


def karsten_tally(good: int, games: int, seed: int = 2022) -> Tally:
    """``games`` games under Karsten's rule, for one number of sources.

    The tally numbers the lands as `engine_tally` does - the colour's lands
    below ``good`` - so `Tally.chance` is asked about ``good`` alone.
    """
    rng = random.Random(seed)
    tally = Tally()
    for _ in range(games):
        hand, top = _karsten_hand(rng, good)
        numbers = sorted(card for card in hand if card < LANDS)
        for turn in range(1, LAST_TURN + 1):
            if top[turn - 1] < LANDS:
                numbers.append(top[turn - 1])
                numbers.sort()
            tally.add(turn, numbers)
    return tally
