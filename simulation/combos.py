"""Watching for a combo while a game is played.

This is the engine half of "how long does it take to come together". It knows
nothing about Commander Spellbook, about Django or about where a combo came
from: it is handed a list of `Watch` objects - a name, a zone and a quantity
per card - and it reports the turn on which each one first came together.

Three rules are enforced here rather than at the call site, because they are
the difference between a number and a number that is true:

**A zone is not a synonym for "drawn".** A card that has to be on the
battlefield is not assembled by sitting in hand, and a `Gravecrawler` that has
to be in the graveyard is not assembled by being in the library. Measuring
"the deck has seen these cards" instead would overstate every percentage on the
page, and would do it silently.

**A zone this engine does not have makes the combo unmeasurable, not
approximate.** Spellbook's vocabulary includes the stack, which in a goldfish
simulation is never a resting place: nothing is ever on it at the end of a
turn. A requirement that can only be met there raises :class:`Unmeasurable`,
and the combo gets no number at all rather than a zero that reads like an
answer.

**The first turn is the one that counts.** A combo that assembles on turn four
and is broken up on turn five assembled on turn four. The watcher records the
earliest turn each combo was whole and stops looking at it.
"""

from collections import Counter
from dataclasses import dataclass

#: Spellbook's zone letters, which are the ones this module speaks.
BATTLEFIELD = "B"
GRAVEYARD = "G"
HAND = "H"
EXILE = "E"
LIBRARY = "L"
COMMAND = "C"

#: Every zone a `Game` can answer for. Deliberately not "every zone Spellbook
#: names": `S`, the stack, is real in Magic and meaningless in a goldfish
#: snapshot taken at the end of a turn.
KNOWN_ZONES = frozenset({BATTLEFIELD, GRAVEYARD, HAND, EXILE, LIBRARY, COMMAND})

#: Where a card is assumed to have to be when nobody said. Battlefield, because
#: that is where all but a handful of Spellbook's requirements are, and because
#: it is the *strictest* reading available - a wrong guess here costs a combo
#: its percentage rather than inventing one.
DEFAULT_ZONES = (BATTLEFIELD,)


class Unmeasurable(ValueError):
    """A combo this engine cannot honestly watch for.

    Raised rather than returned, and caught where the watch is built. The
    caller's job is then to say *why* there is no number beside a combo, which
    is a sentence a person can act on; a silent zero is not.
    """


@dataclass(frozen=True)
class Requirement:
    """One card a combo needs, and where it has to be.

    `zones` is a list of alternatives, as Spellbook means it: a card whose
    zones are `("B", "G")` counts on the battlefield *or* in the graveyard.
    """

    name: str
    zones: tuple[str, ...] = DEFAULT_ZONES
    quantity: int = 1
    must_be_commander: bool = False

    def __post_init__(self):
        if not self.name:
            raise Unmeasurable("a requirement needs a card name")
        if not self.zones:
            raise Unmeasurable(f"{self.name}: no zone was given")
        unknown = sorted(set(self.zones) - KNOWN_ZONES)
        if unknown:
            raise Unmeasurable(
                f"{self.name}: this engine has no {', '.join(unknown)} zone"
            )
        if self.quantity < 1:
            raise Unmeasurable(f"{self.name}: a quantity below one")


@dataclass(frozen=True)
class Watch:
    """One combo, as something a game can be asked about."""

    key: str
    requirements: tuple[Requirement, ...]
    #: Whether the combo ends the game once it is together (P8): the bracket
    #: check times these and only these. A loop that makes mana or tokens and
    #: wins nothing on its own would make a deck look faster than it is.
    wins: bool = False

    def __post_init__(self):
        if not self.requirements:
            raise Unmeasurable(f"{self.key}: nothing to watch for")


class Watcher:
    """Every combo being watched in one game, and when each came together.

    Built once per game. It counts only the cards and only the zones the
    watches actually name, which is what keeps it cheap: a deck has a hundred
    cards and a combo has two or three, so counting the whole library every
    turn would cost forty times what this does for the same answer.
    """

    __slots__ = ("watches", "names", "zones", "first", "_outstanding")

    def __init__(self, watches):
        self.watches = tuple(watches)
        self.names = frozenset(
            requirement.name
            for watch in self.watches
            for requirement in watch.requirements
        )
        self.zones = frozenset(
            zone
            for watch in self.watches
            for requirement in watch.requirements
            for zone in requirement.zones
        )
        #: Turn numbers, one-based. Zero means "not in the turns played", which
        #: is the answer for most combos in most games and has to survive as
        #: itself rather than as a missing key.
        self.first = {watch.key: 0 for watch in self.watches}
        self._outstanding = len(self.watches)

    def __bool__(self) -> bool:
        return bool(self.watches)

    @property
    def first_win(self) -> int:
        """The first turn a game-ending combo was together; zero if none was.

        The earliest over the combos rather than per combo: two combos that
        each come together in half the games can between them come together
        in all of them, and only the game knows which.
        """
        return min((self.first[watch.key] for watch in self.watches
                    if watch.wins and self.first[watch.key]), default=0)

    def look(self, game, turn: int) -> None:
        """Record any watch that has just come together, at the end of `turn`."""
        if not self._outstanding:
            return
        where = self.snapshot(game)
        for watch in self.watches:
            if self.first[watch.key]:
                continue
            if self._met(watch, where, game):
                self.first[watch.key] = turn
                self._outstanding -= 1

    def snapshot(self, game) -> dict:
        """How many of each watched card sits in each watched zone."""
        where = {}
        if BATTLEFIELD in self.zones or COMMAND in self.zones:
            battlefield = self._count(game.battlefield)
            where[BATTLEFIELD] = battlefield
            if COMMAND in self.zones:
                # The command zone is not a list on the game: the commander is
                # in it whenever it is not on the battlefield. The engine never
                # puts a commander anywhere else - it cannot die in a goldfish
                # game - so this is the whole of the rule.
                commander = game.deck.commander
                where[COMMAND] = Counter()
                if (commander is not None
                        and commander.name in self.names
                        and not battlefield.get(commander.name)):
                    where[COMMAND][commander.name] = 1
        if GRAVEYARD in self.zones:
            where[GRAVEYARD] = self._count(game.graveyard)
        if HAND in self.zones:
            where[HAND] = self._count(game.hand)
        if EXILE in self.zones:
            where[EXILE] = self._count(game.exiled)
        if LIBRARY in self.zones:
            where[LIBRARY] = self._count(game.library)
        return where

    def _count(self, cards) -> Counter:
        counts = Counter()
        for card in cards:
            if card.name in self.names:
                counts[card.name] += 1
        return counts

    def _met(self, watch: Watch, where: dict, game) -> bool:
        for requirement in watch.requirements:
            if requirement.must_be_commander:
                commander = game.deck.commander
                if commander is None or commander.name != requirement.name:
                    return False
            held = sum(
                where.get(zone, {}).get(requirement.name, 0)
                for zone in requirement.zones
            )
            if held < requirement.quantity:
                return False
        return True
