"""How much: one amount (card model, phase 4).

A counting mana ability, a ritual that counts and Cabal Coffers each said how
much they make in a string language of their own: ``basic:swamp``,
``graveyard:B``, ``devotion:G``, ``names:a|b``, ``power:greatest``,
``tapped:island``, ``threshold:7:5``. An `Amount` says it in one form, and
`compile_amount` turns one into a function of the game and the card once.

The kinds:

* ``fixed`` - ``n``
* ``count`` - the cards of ``zone`` that ``selector`` selects, less ``less``
  (never below none), plus ``n``
* ``devotion`` - your devotion to ``color``
* ``best_devotion`` - your devotion to the colour you are most devoted to,
  ``color`` first on a tie; the colour comes with it
* ``stat`` - ``stat`` of the card or the board: ``power``, ``counters``,
  ``power:greatest``, ``toughness:greatest_other``; v34: ``toughness:greatest``
  (the card's own counted), ``mv:greatest`` among the permanents ``selector``
  selects, ``sacrificed:power`` and ``sacrificed:mv`` of what the last
  additional cost sacrificed (Life's Legacy)

v34: ``half`` halves a count, rounded down (Imskir Iron-Eater).
* ``all_named`` - ``n`` when you control lands of every one of ``names``
  besides this one, else none
* ``threshold`` - ``more`` once ``zone`` holds ``at_least`` cards, else ``n``

The zones: ``creatures``, ``lands``, ``battlefield``, ``graveyard``,
``untapped_lands`` (the lands still untapped this turn) and ``land_types``
(every land by its land types, the granted ones included).
"""

from dataclasses import dataclass

from simulation.manacost import COLORS
from simulation.permanent import same
from simulation.rules.selector import Selector, compile_selector


@dataclass(frozen=True)
class Amount:
    kind: str = "fixed"
    n: int = 0
    zone: str = ""
    selector: Selector | None = None
    less: int = 0
    color: str = ""
    stat: str = ""
    names: frozenset[str] = frozenset()
    at_least: int = 0
    more: int = 0
    half: bool = False


def compile_amount(amount: Amount):
    """``amount`` as a function ``(game, card) -> int``, built once.

    ``best_devotion`` gives ``(int, colour)`` instead: its colour is part of
    the answer.
    """
    return _KINDS[amount.kind](amount)


def _fixed(amount):
    n = amount.n
    return lambda game, card: n


def _zone(game, zone: str, card):
    if zone == "creatures":
        return game.creatures
    if zone == "lands":
        return game.lands
    if zone == "battlefield":
        return game.battlefield
    if zone == "graveyard":
        return game.graveyard
    if zone == "untapped_lands":
        return game._untapped(game.lands.untapped())
    raise ValueError(f"no such zone: {zone}")


def _count(amount):
    zone, n, less = amount.zone, amount.n, amount.less
    if zone in ("untapped_lands", "land_types"):
        # Land types as they are now: Urborg makes every land a Swamp.
        wanted = amount.selector.subtypes
        lands = "lands" if zone == "land_types" else zone

        def by_type(game, card) -> int:
            from simulation.mana import granted_subtypes, subtypes_of

            granted = granted_subtypes(game.lands)
            found = sum(1 for land in _zone(game, lands, card)
                        if wanted & subtypes_of(land, granted))
            return max(found - less, 0) + n
        return by_type
    selects = compile_selector(amount.selector or Selector())

    half = amount.half

    def count(game, card) -> int:
        found = sum(1 for each in _zone(game, zone, card) if selects(each))
        found = max(found - less, 0) + n
        return found // 2 if half else found
    return count


def devotion(game, color: str) -> int:
    """Mana symbols of a colour among the costs of your permanents (Nykthos)."""
    total = 0
    for permanent in game.battlefield:
        if permanent.is_land:
            continue
        cost = permanent.mana_cost
        total += cost.colored.get(color, 0)
        total += sum(1 for symbol in cost.hybrid if color in symbol.colors)
        total += sum(1 for symbol in cost.phyrexian if symbol == color)
    return total


def _devotion(amount):
    color = amount.color
    return lambda game, card: devotion(game, color)


def _best_devotion(amount):
    tie = amount.color

    def best(game, card):
        found = {each: devotion(game, each) for each in COLORS}
        color = max(COLORS, key=lambda each: (found[each], each == tie))
        return found[color], color
    return best


def _stat(amount):
    stat = amount.stat
    selects = compile_selector(amount.selector or Selector())

    def value(game, card) -> int:
        if stat == "power":
            return game.power(card)
        if stat == "counters":
            return game.plus(card)
        if stat == "power:greatest":
            return max((game.power(each) for each in game.creatures), default=0)
        if stat == "toughness:greatest":
            return max((game.toughness(each) for each in game.creatures), default=0)
        if stat == "mv:greatest":
            return max((each.mv for each in game.battlefield if selects(each)), default=0)
        if stat.startswith("sacrificed:"):
            gone = game.last_sacrificed
            if gone is None or isinstance(gone, str):
                return 0
            return gone.power if stat == "sacrificed:power" else gone.mv
        return max((game.toughness(each) for each in game.creatures
                    if not same(each, card)), default=0)
    return value


def _all_named(amount):
    names, n = amount.names, amount.n

    def value(game, card) -> int:
        here = {land.name for land in game.lands if not same(land, card)}
        return n if names <= here else 0
    return value


def _threshold(amount):
    zone, at_least, more, n = amount.zone, amount.at_least, amount.more, amount.n

    def value(game, card) -> int:
        return more if len(_zone(game, zone, card)) >= at_least else n
    return value


_KINDS = {"fixed": _fixed, "count": _count, "devotion": _devotion,
          "best_devotion": _best_devotion, "stat": _stat, "all_named": _all_named,
          "threshold": _threshold}

KINDS = tuple(_KINDS)
