"""Permanents, and the one rule that says whose state is whose (card model, phase 4).

Until phase 4 the battlefield was four lists of card objects shared by every
copy of a card, and the state of a permanent - arrived this turn, tapped,
its counters, what is attached to it - was kept by the card's **name**. Two
copies of a card were one object and one name, so two Germs shared their
counters and their equipment.

PR 12 changes no game. It gives every permanent an object of its own
(`Permanent`, with a number counted per game) and puts the two questions the
engine asks behind one rule each:

* `key` - under which key a permanent's state is kept. Today the card's name.
* `same` - whether two are one permanent. Today the same card object;
  `ident` is the same question as a value a set can hold.

The marks kept by name (`Marks`) go through `key` and nothing else. PR 17
changes `key` to the permanent's number and `same` to the permanent, and
every copy then keeps its own state: one place each, not sixty.

The four battlefield lists stay lists of cards, in today's order, because
the agent, the analysis, the combos, the playtest board and the tests read
cards. Each is a `Zone`: a list that keeps its permanents beside its cards,
so reading it costs what reading a list costs, and every way of changing it
- the game's own moves, an action, a test that writes ``game.lands = [...]``
- keeps both in step.

**Nothing here imports Django**, like the rest of :mod:`simulation`.
"""

from collections import Counter


def key(card) -> str:
    """The key a permanent's state is kept under: today its card's name."""
    return card.name


def same(one, other) -> bool:
    """Whether two are one permanent: today the same card object."""
    return one is other


def ident(card) -> int:
    """`same` as a value to keep in a set or a dictionary."""
    return id(card)


class Permanent:
    """One permanent on the battlefield: its card and its number in the game."""

    __slots__ = ("card", "pid")

    def __init__(self, card, pid: int):
        self.card = card
        self.pid = pid

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
        self.permanents = [Permanent(card, game.next_pid()) for card in self]

    def _new(self, card) -> Permanent:
        return Permanent(card, self._game.next_pid())

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
    """The state `Game` keeps for permanents by `key`, as named questions.

    The ten attributes themselves stay on the game with today's names and
    meaning, because a stored playtest and the trace write them: lists where
    a count by key matters (a creature tapped twice, once for another card's
    mana), and marks that outlive the permanent (a store emptied this turn).
    """

    # --- arrived and tapped creatures ---------------------------------------

    def arrived_now(self, card) -> bool:
        """Did it enter this turn?"""
        return key(card) in self.arrived

    def mark_arrived(self, card) -> None:
        self.arrived.append(key(card))

    def creature_tapped(self, card) -> bool:
        """Was its {T} used this turn?"""
        return key(card) in self.tapped_creatures

    def tap_creature(self, card) -> None:
        self.tapped_creatures.append(key(card))

    def fodder_tapped(self, card) -> bool:
        """Was it tapped for another card's mana this turn?"""
        return key(card) in self.fodder_used

    def tap_as_fodder(self, card) -> None:
        """Tapped for another card's mana: tapped, and that card's."""
        self.tapped_creatures.append(key(card))
        self.fodder_used.append(key(card))

    def busy(self) -> Counter:
        """By key, the creatures tapped this turn for anything but another
        card's mana."""
        return Counter(self.tapped_creatures) - Counter(self.fodder_used)

    # --- choices made as it entered ------------------------------------------

    def chosen_color_of(self, card) -> str | None:
        return self.chosen_colors.get(key(card))

    def choose_color(self, card, color: str) -> None:
        self.chosen_colors[key(card)] = color

    def imprinted_on(self, card) -> str:
        """The colours of the card a Chrome Mox exiled; empty for none."""
        return self.imprinted.get(key(card), "")

    def imprint(self, card, colors: str) -> None:
        self.imprinted[key(card)] = colors

    # --- counters ------------------------------------------------------------

    def kept_counters(self, card) -> int:
        """The counters it keeps for its mana."""
        return self.counters.get(key(card), 0)

    def keep_counters(self, card, count: int) -> None:
        self.counters[key(card)] = count

    def add_kept_counter(self, card) -> None:
        self.counters[key(card)] = self.counters.get(key(card), 0) + 1

    def plus(self, card) -> int:
        """Its +1/+1 counters."""
        return self.plus_counters.get(key(card), 0)

    def set_plus(self, card, count: int) -> None:
        """Set its +1/+1 counters; none leaves no entry, as before."""
        if count:
            self.plus_counters[key(card)] = count
        else:
            self.plus_counters.pop(key(card), None)

    def mark_emptied(self, card) -> None:
        """Its last counter went this turn."""
        self.emptied.append(key(card))

    # --- what turned, and what is attached -------------------------------------

    def has_flipped(self, card) -> bool:
        return key(card) in self.flipped

    def mark_flipped(self, card) -> None:
        self.flipped.append(key(card))

    def host_key(self, card) -> str | None:
        """The key of the creature it is attached to, if any."""
        return self.attached.get(key(card))

    def attach(self, card, host) -> None:
        self.attached[key(card)] = key(host)

    def unattach(self, card) -> None:
        self.attached.pop(key(card), None)

    def carries(self, creature) -> bool:
        """Is something attached to it?"""
        return key(creature) in self.attached.values()

    def is_host_of(self, creature, card) -> bool:
        """Is ``card`` attached to ``creature``?"""
        return self.attached.get(key(card)) == key(creature)
