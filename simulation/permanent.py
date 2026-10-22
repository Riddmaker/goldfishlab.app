"""Permanents, and whose state is whose (card model, phase 4).

Until phase 4 the battlefield was four lists of card objects shared by every
copy of a card, and the state of a permanent - arrived this turn, tapped,
its counters, what is attached to it - was kept by the card's **name**. Two
copies of a card were one object and one name, so two Germs shared their
counters and their equipment.

PR 12 gave every permanent an object of its own (`Permanent`, with a number
counted per game). PR 17 keeps every mark by that number:

* each copy of a card in a game is an object of its own (`Card.twin`), so
  `same` - whether two are one permanent - is whether they are one object,
  and `ident` is that question as a value a set can hold;
* `Marks` keeps a permanent's state under its number (`Marks.pid_of`), so a
  copy, a Germ or a card that left and came back starts with none.

The four battlefield lists stay lists of cards, in today's order, because
the agent, the analysis, the combos, the playtest board and the tests read
cards. Each is a `Zone`: a list that keeps its permanents beside its cards,
so reading it costs what reading a list costs, and every way of changing it
- the game's own moves, an action, a test that writes ``game.lands = [...]``
- keeps both in step, and keeps the game's index from card to permanent.

**Nothing here imports Django**, like the rest of :mod:`simulation`.
"""

import copy
from collections import Counter


def same(one, other) -> bool:
    """Whether two are one permanent: the same card object."""
    return one is other


def ident(card) -> int:
    """`same` as a value to keep in a set or a dictionary."""
    return id(card)


class Permanent:
    """One permanent on the battlefield: its card, its number in the game,
    whether it is tapped, and whether it is held tapped - a Mana Vault used
    once does not untap in its controller's untap step."""

    __slots__ = ("card", "pid", "tapped", "held")

    def __init__(self, card, pid: int):
        self.card = card
        self.pid = pid
        self.tapped = False
        self.held = False

    def __repr__(self) -> str:
        return f"Permanent({self.card.name!r}, {self.pid})"


class Zone(list):
    """A battlefield list of cards that keeps a `Permanent` beside each.

    A card that comes in gets a new permanent with the game's next number;
    one moved inside the zone (`move`) keeps its own. Sorting and reversing
    carry the permanents along. Slices and copies are plain lists of cards.
    """

    __slots__ = ("permanents", "_game")

    def __init__(self, game, cards=()):
        super().__init__(cards)
        self._game = game
        self.permanents = [self._new(card) for card in self]

    def _new(self, card) -> Permanent:
        """A permanent for a card that comes in, in the game's index."""
        permanent = Permanent(card, self._game.next_pid())
        self._game.where[id(card)] = permanent
        self._game.pid_names[permanent.pid] = card.name
        return permanent

    def __deepcopy__(self, memo):
        """A copy of the game's zone for a copy of the game (the stress test):
        the same cards and copies of their permanents, numbers kept - never
        new ones, as `append` would give."""
        copied = Zone.__new__(Zone)
        memo[id(self)] = copied
        list.extend(copied, self)
        copied._game = copy.deepcopy(self._game, memo)
        copied.permanents = copy.deepcopy(self.permanents, memo)
        return copied

    def append(self, card) -> None:
        super().append(card)
        self.permanents.append(self._new(card))

    def extend(self, cards) -> None:
        cards = list(cards)
        super().extend(cards)
        self.permanents.extend(self._new(card) for card in cards)

    def __iadd__(self, cards):
        self.extend(cards)
        return self

    def insert(self, index: int, card) -> None:
        super().insert(index, card)
        self.permanents.insert(index, self._new(card))

    def pop(self, index: int = -1):
        card = super().pop(index)
        self.permanents.pop(index)
        return card

    def index(self, card, *args) -> int:
        """Where ``card`` is: this very object first, so of two equal copies
        the one asked about; an equal card if the object is not here."""
        found = next((at for at, each in enumerate(self) if each is card), None)
        return super().index(card, *args) if found is None or args else found

    def remove(self, card) -> None:
        del self[self.index(card)]

    def clear(self) -> None:
        super().clear()
        self.permanents.clear()

    def __delitem__(self, index) -> None:
        super().__delitem__(index)
        del self.permanents[index]

    def __setitem__(self, index, value) -> None:
        super().__setitem__(index, value)
        if isinstance(index, slice):
            self.permanents[index] = [self._new(card) for card in value]
        else:
            self.permanents[index] = self._new(value)

    def __imul__(self, times):
        raise TypeError("a zone cannot be multiplied")

    def sort(self, *, key=None, reverse: bool = False) -> None:
        pairs = list(zip(self, self.permanents, strict=True))
        pairs.sort(key=(lambda pair: key(pair[0])) if key else (lambda pair: pair[0]),
                   reverse=reverse)
        super().__setitem__(slice(None), [card for card, _permanent in pairs])
        self.permanents[:] = [permanent for _card, permanent in pairs]

    def reverse(self) -> None:
        super().reverse()
        self.permanents.reverse()

    def move(self, start: int, to: int) -> None:
        """Move the permanent at ``start`` to ``to``: the same permanent, as
        a land that turns tapped moves to the front without leaving."""
        card = super().pop(start)
        permanent = self.permanents.pop(start)
        super().insert(to, card)
        self.permanents.insert(to, permanent)

    # --- tapped ---------------------------------------------------------------

    @property
    def tapped_count(self) -> int:
        return sum(permanent.tapped for permanent in self.permanents)

    def untapped(self) -> list:
        """The cards of the untapped permanents, in the zone's order."""
        return [card for card, permanent in zip(self, self.permanents, strict=True)
                if not permanent.tapped]

    def tap(self, index: int, *, first: bool = False) -> None:
        """Tap the permanent at ``index``. Tapped permanents stand before the
        untapped ones, as the board shows them: this one goes first, or after
        the others tapped already."""
        to = 0 if first else sum(permanent.tapped for at, permanent
                                 in enumerate(self.permanents) if at != index)
        self.permanents[index].tapped = True
        self.move(index, to)

    def untap(self) -> None:
        """The untap step. A held permanent stays out of the mana all the
        same: `Game` asks `held` beside `tapped`."""
        for permanent in self.permanents:
            permanent.tapped = False

    def in_step(self) -> bool:
        """Whether each permanent holds the card at its place."""
        return len(self) == len(self.permanents) and all(
            permanent.card is card for card, permanent in zip(self, self.permanents,
                                                              strict=True))


class ZoneField:
    """A `Game` attribute that makes whatever list it is given a `Zone`.

    It defines only ``__set__``: reading the attribute finds the zone in the
    instance's dictionary as any attribute, so the engine's many reads cost
    no call; only a write goes through here.
    """

    def __set_name__(self, owner, name: str) -> None:
        self.name = name

    def __set__(self, game, cards) -> None:
        game.__dict__[self.name] = Zone(game, cards)


class Marks:
    """The state `Game` keeps for permanents by number, as named questions.

    The ten attributes stay on the game with their names, because a stored
    playtest and the trace write them: lists where a count matters (a
    creature tapped twice, once for another card's mana), and marks that
    outlive the permanent (a store emptied this turn). A card that left the
    battlefield keeps the number it had until it comes in again, so what
    happens as it leaves still finds its state; coming back, it is a new
    permanent with none.
    """

    def pid_of(self, card) -> int | None:
        """The number of the permanent ``card`` is or last was; None for a
        card that never was one."""
        permanent = self.where.get(id(card))
        return None if permanent is None or permanent.card is not card else permanent.pid

    def _pid(self, card) -> int:
        found = self.pid_of(card)
        if found is None:
            raise ValueError(f"{card.name} has never been on the battlefield")
        return found

    def permanent_by(self, pid, zones) -> object | None:
        """The card of permanent ``pid`` in one of ``zones``, if it is there."""
        for zone in zones:
            for card, permanent in zip(zone, zone.permanents, strict=True):
                if permanent.pid == pid:
                    return card
        return None

    # --- arrived and tapped creatures ---------------------------------------

    def arrived_now(self, card) -> bool:
        """Did it enter this turn?"""
        return self.pid_of(card) in self.arrived

    def mark_arrived(self, card) -> None:
        self.arrived.append(self._pid(card))

    def creature_tapped(self, card) -> bool:
        """Was its {T} used this turn?"""
        return self.pid_of(card) in self.tapped_creatures

    def tap_creature(self, card) -> None:
        self.tapped_creatures.append(self._pid(card))

    def fodder_tapped(self, card) -> bool:
        """Was it tapped for another card's mana this turn?"""
        return self.pid_of(card) in self.fodder_used

    def tap_as_fodder(self, card) -> None:
        """Tapped for another card's mana: tapped, and that card's."""
        self.tapped_creatures.append(self._pid(card))
        self.fodder_used.append(self._pid(card))

    def busy(self) -> Counter:
        """By number, the creatures tapped this turn for anything but another
        card's mana."""
        return Counter(self.tapped_creatures) - Counter(self.fodder_used)

    # --- choices made as it entered ------------------------------------------

    def chosen_color_of(self, card) -> str | None:
        return self.chosen_colors.get(self.pid_of(card))

    def choose_color(self, card, color: str) -> None:
        self.chosen_colors[self._pid(card)] = color

    def imprinted_on(self, card) -> str:
        """The colours of the card a Chrome Mox exiled; empty for none."""
        return self.imprinted.get(self.pid_of(card), "")

    def imprint(self, card, colors: str) -> None:
        self.imprinted[self._pid(card)] = colors

    # --- counters ------------------------------------------------------------

    def kept_counters(self, card) -> int:
        """The counters it keeps for its mana."""
        return self.counters.get(self.pid_of(card), 0)

    def keep_counters(self, card, count: int) -> None:
        self.counters[self._pid(card)] = count

    def add_kept_counter(self, card) -> None:
        pid = self._pid(card)
        self.counters[pid] = self.counters.get(pid, 0) + 1

    def plus(self, card) -> int:
        """Its +1/+1 counters."""
        return self.plus_counters.get(self.pid_of(card), 0)

    def set_plus(self, card, count: int) -> None:
        """Set its +1/+1 counters; none leaves no entry, as before."""
        if count:
            self.plus_counters[self._pid(card)] = count
        else:
            self.plus_counters.pop(self.pid_of(card), None)

    def mark_emptied(self, card) -> None:
        """Its last counter went this turn."""
        self.emptied.append(self._pid(card))

    # --- what turned, and what is attached -------------------------------------

    def has_flipped(self, card) -> bool:
        return self.pid_of(card) in self.flipped

    def mark_flipped(self, card) -> None:
        self.flipped.append(self._pid(card))

    def host_pid(self, card) -> int | None:
        """The number of the creature it is attached to, if any."""
        return self.attached.get(self.pid_of(card))

    def attach(self, card, host) -> None:
        self.attached[self._pid(card)] = self._pid(host)

    def unattach(self, card) -> None:
        self.attached.pop(self.pid_of(card), None)

    def carries(self, creature) -> bool:
        """Is something attached to it?"""
        return self.pid_of(creature) in self.attached.values()

    def is_host_of(self, creature, card) -> bool:
        """Is ``card`` attached to ``creature``?"""
        host = self.attached.get(self.pid_of(card))
        return host is not None and host == self.pid_of(creature)
