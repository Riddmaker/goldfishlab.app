"""Whether something about the board holds: one condition (card model, phase 4).

A `Condition` is the question a land that would enter tapped asks, and the
one a mana ability asks before it may be activated: the kinds below, each
with the numbers and types it needs. Until phase 4 `Game.holds` answered them
by reading the kind every time; `compile_condition` turns a condition into a
function of the game, the card and ``entering`` once, and `Game.holds` only
calls it.

``entering`` means the card is asked about before it is on the battlefield -
a land deciding whether it enters tapped, a land the agent thinks of playing
- and so counts itself in. Otherwise it is already among the permanents:
Temple of the False God is one of the five lands it asks for.

The kinds:

* ``control_type`` - you control a land of one of ``types``
* ``lands`` - you control ``count`` or more (``at_least``) or at most
  ``count`` lands, ``other`` than this one, ``basic`` ones, or of ``type``
* ``artifacts`` - you control ``count`` or more artifacts
* ``opponents`` - you have ``count`` or more opponents (a table has three)
* ``permanent`` - you control another permanent of one of ``types``, a
  ``legendary`` one if set
* ``turn`` - it is your turn ``count`` or earlier
* ``opponent_lands`` - your opponents control ``count`` or more lands,
  assuming each of three plays one a turn
* ``life_at_most`` - you have ``count`` or less life
* ``power`` - you control a creature with power ``count`` or greater
* ``reveal`` - you can reveal another card of one of ``types`` from your hand
* ``pay_life`` - you pay ``count`` life, never below the Phyrexian floor
* ``tap_fodder`` - a permanent of one of ``types`` (a ``legendary`` one, or of
  ``type``) is there for this card to tap besides itself

An unknown kind never holds.
"""

from dataclasses import dataclass, field

from simulation.manacost import PHYREXIAN_LIFE_FLOOR
from simulation.permanent import ident, same
from simulation.rules.selector import compile_selector, types_of, wanted

ARTIFACT = "artifact"
CREATURE = "creature"


@dataclass(frozen=True)
class Condition:
    """One question about the board; the fields a kind does not use stay empty."""

    kind: str
    types: frozenset[str] = field(default_factory=frozenset)
    count: int = 0
    at_least: bool = True
    other: bool = False
    basic: bool = False
    type: str = ""
    legendary: bool = False

    @classmethod
    def of(cls, value) -> "Condition":
        """The same question from any object with these fields: a land's
        ``TappedUnless``, until its family moves (PR 14)."""
        if isinstance(value, cls):
            return value
        return cls(kind=value.kind, types=frozenset(value.types), count=value.count,
                   at_least=value.at_least, other=value.other, basic=value.basic,
                   type=value.type, legendary=value.legendary)


def tap_wanted(condition: Condition) -> str:
    """What a ``tap_fodder`` condition asks the permanent to be besides its
    type: ``legendary``, a colour letter, a creature type or ""."""
    return "legendary" if condition.legendary else condition.type


def compile_condition(condition: Condition):
    """``condition`` as a function ``(game, card, entering) -> bool``, built once."""
    build = _KINDS.get(condition.kind)
    if build is None:
        return lambda game, card, entering=False: False
    return build(condition)


def _control_type(condition):
    types = condition.types
    return lambda game, card, entering=False: any(
        other.subtypes & types for other in game.lands)


def _lands(condition):
    basic, kind, count = condition.basic, condition.type, condition.count
    other, at_least = condition.other, condition.at_least

    def matches(land) -> bool:
        return (not basic or land.basic) and (not kind or kind in land.subtypes)

    def holds(game, card, entering=False) -> bool:
        counted = [land for land in game.lands if matches(land)]
        if entering:
            # Itself only when it is one of them: a battle land is not a
            # basic land, so it needs two others (fixed in P19 R12).
            have = len(counted) + (0 if other or not matches(card) else 1)
        else:
            itself = any(same(land, card) for land in counted)
            have = len(counted) - (1 if other and itself else 0)
        return have >= count if at_least else have <= count
    return holds


def _artifacts(condition):
    count = condition.count

    def holds(game, card, entering=False) -> bool:
        have = sum(1 for permanent in game.battlefield if ARTIFACT in permanent.types)
        if entering and ARTIFACT in card.types:
            have += 1
        return have >= count
    return holds


def _opponents(condition):
    count = condition.count
    return lambda game, card, entering=False: game.OPPONENTS >= count


def _permanent(condition):
    types, legendary = condition.types, condition.legendary
    return lambda game, card, entering=False: any(
        permanent.types & types and (permanent.legendary or not legendary)
        for permanent in game.battlefield if not same(permanent, card))


def _turn(condition):
    count = condition.count
    return lambda game, card, entering=False: game.turn <= count


def _opponent_lands(condition):
    count = condition.count
    # An assumption, stated on the card page: each opponent has played a
    # land in each of their turns before this one of yours.
    return lambda game, card, entering=False: game.OPPONENTS * max(game.turn - 1, 0) >= count


def _life_at_most(condition):
    count = condition.count
    return lambda game, card, entering=False: game.life <= count


def _power(condition):
    count = condition.count
    return lambda game, card, entering=False: any(
        CREATURE in types_of(permanent) and game.power(permanent) >= count
        for permanent in game.battlefield)


def _reveal(condition):
    types = condition.types
    # A land type (the Castles), or since engine version 28 a creature type:
    # Gilt-Leaf Palace's Elf. Another card: never the land itself.
    return lambda game, card, entering=False: any(
        other is not card and (other.subtypes | other.creature_types) & types
        for other in game.hand)


def _pay_life(condition):
    count = condition.count
    return lambda game, card, entering=False: game.life - count >= PHYREXIAN_LIFE_FLOOR


def _tap_fodder(condition):
    tappable = compile_selector(wanted(condition.types, tap_wanted(condition)))

    def holds(game, card, entering=False) -> bool:
        # P19 R19: something left for it to tap, see `Game.tap_fodder`.
        if entering:
            _, left = game.tap_fodder()
            return any(tappable(found) for found in left)
        return bool(game.tap_fodder()[0].get(ident(card)))
    return holds


_KINDS = {
    "control_type": _control_type, "lands": _lands, "artifacts": _artifacts,
    "opponents": _opponents, "permanent": _permanent, "turn": _turn,
    "opponent_lands": _opponent_lands, "life_at_most": _life_at_most, "power": _power,
    "reveal": _reveal, "pay_life": _pay_life, "tap_fodder": _tap_fodder,
}

#: Every kind a condition may have.
KINDS = tuple(_KINDS)

_COMPILED: dict = {}


def check(condition):
    """The compiled form of a condition (or a ``TappedUnless``), compiled the
    first time it is asked."""
    found = _COMPILED.get(condition)
    if found is None:
        found = _COMPILED[condition] = compile_condition(Condition.of(condition))
    return found
