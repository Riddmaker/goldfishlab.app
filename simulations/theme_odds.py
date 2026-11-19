"""The chance that a theme stands by turn four (deck page v2, PR E).

A theme stands when at least one card of each of its pieces has been seen:
the opening hand of seven and one draw a turn from turn two, no mulligan -
the same assumptions as the strategies' odds (`simulations.summary`).

The pieces share cards (Blood Artist is a payoff and a body), so the odds of
each piece cannot be multiplied. Inclusion and exclusion over the pieces
gives the exact answer instead of a simulation's estimate:

    P(every piece seen) = sum over sets S of pieces of
                          (-1)^|S| * P(no card of the union of S seen)

and "no card of a set seen" is hypergeometric. A theme has at most a handful
of pieces, so the sum has a few dozen terms. A piece the commander holds is
always there: the commander starts in the command zone, never in the library.
"""

from itertools import combinations

from simulations.report import hypergeometric
from simulations.summary import BY_TURN, SEEN_BY_TURN


def stands_by(found, readings, seen: int = SEEN_BY_TURN) -> float:
    """The chance, in percent with one decimal, that `found` (a
    `cards.themes.Found`) has a card of every piece among the first `seen`
    cards of the library."""
    commanders = set()
    card_of = {}
    quantity = {}
    population = 0
    for reading in readings:
        oracle = reading.oracle_card
        names = {oracle.name, oracle.front_name}
        if reading.is_commander:
            commanders |= names
            continue
        population += reading.quantity
        quantity[oracle.pk] = reading.quantity
        card_of.update(dict.fromkeys(names, oracle.pk))
    if population == 0:
        return 0.0
    seen = min(seen, population)
    pieces = [{card_of[name] for name in part.cards if name in card_of}
              for part in found.parts if not commanders & set(part.cards)]
    if any(not piece for piece in pieces):
        return 0.0
    total = 0.0
    for size in range(len(pieces) + 1):
        for chosen in combinations(pieces, size):
            cards = sum(quantity[pk] for pk in set().union(*chosen))
            total += (-1) ** size * hypergeometric(0, population, cards, seen) / 100
    return round(100 * max(0.0, min(1.0, total)), 1)


def groups(themes, readings) -> list[tuple]:
    """(found, percent) for each theme, as the deck page lists them."""
    return [(found, stands_by(found, readings)) for found in themes]


__all__ = ["BY_TURN", "groups", "stands_by"]
