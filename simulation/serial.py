"""Turning engine objects into something a database row can hold, and back.

Phase 5 stores a playtest as **the seeded shuffle plus the actions taken**, and
replays it on demand. Two things therefore have to survive a round trip through
JSON: the deck the session started with, and - as a cache, never as the truth -
the game state at a given action.

The deck is snapshotted rather than re-derived, and that is a deliberate
decision rather than an optimisation. A deck's cards, and the annotations that
say how the engine should read them, can be edited while a session is open. If
the session re-derived its deck on every request, a card would change shape
under the player's hands halfway through a game. The same reasoning already
applies to a stored :class:`~simulations.models.SimulationRun`, which keeps the
gaps it was computed with.

**Nothing here imports Django**, like the rest of :mod:`simulation`. It is
plain dataclass reflection, which is also why it does not need maintaining: a
field added to :class:`~simulation.cards.Card` is serialised by the same code
that serialises the rest, and :mod:`tests.test_serial` asserts exactly that.
"""

import dataclasses
import random
import types
import typing

from simulation.cards import Card, DeckDefinition
from simulation.game import Game
from simulation.mana import ManaPool, RestrictedMana, Stored

#: Dataclasses reachable from a deck, by name. Loading consults this rather
#: than anything importable, so a crafted payload cannot name an arbitrary
#: class - the game state comes back out of a database we wrote, but it is
#: still input, and input picks from a list.
_TYPES: dict[str, type] = {}


def _register(cls: type) -> type:
    _TYPES[cls.__name__] = cls
    return cls


def fields_of(cls) -> list[tuple[str, object]]:
    """The fields a dump of ``cls`` holds, with their types, in order.

    A `Card` is written as the fields it is made with: its mana family as
    the sixteen fields it takes (``InitVar``), which make its blocks again
    when it is loaded, and not the blocks themselves (card model phase 4,
    PR 13). So a stored deck or game keeps one form for every card, from
    before PR 13 and after.
    """
    found = _FIELDS.get(cls)
    if found is None:
        if cls is Card:
            found = [(name, item.type.type if isinstance(item.type, dataclasses.InitVar)
                      else item.type)
                     for name, item in Card.__dataclass_fields__.items()
                     if name != "abilities"]
        else:
            found = [(item.name, item.type) for item in dataclasses.fields(cls)]
        _FIELDS[cls] = found
    return found


_FIELDS: dict[type, list] = {}


def _discover() -> None:
    """Register every dataclass a `Card` can reach, transitively."""
    pending = [Card, DeckDefinition]
    while pending:
        cls = pending.pop()
        if cls.__name__ in _TYPES:
            continue
        _register(cls)
        for _name, annotation in fields_of(cls):
            for found in _dataclasses_in(annotation):
                pending.append(found)


def _dataclasses_in(annotation) -> list[type]:
    """Every dataclass mentioned by a type annotation, however nested."""
    if dataclasses.is_dataclass(annotation):
        return [annotation]
    return [found
            for arg in typing.get_args(annotation)
            for found in _dataclasses_in(arg)]


_discover()


def engine_type(name: str) -> type | None:
    """The engine dataclass a dump names in ``_type``, if it is one of ours."""
    return _TYPES.get(name)


# --- Dumping ---------------------------------------------------------------

def dump(value):
    """One engine value, as something `json.dumps` will accept.

    Dataclasses carry their type name so that loading knows what to rebuild.
    Tuples and frozensets become lists; the annotation on the other side says
    what they were, so the shape is not lost.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {"_type": type(value).__name__}
        for name, _annotation in fields_of(type(value)):
            out[name] = dump(getattr(value, name))
        return out
    if isinstance(value, (list, tuple, frozenset, set)):
        return [dump(item) for item in value]
    if isinstance(value, dict):
        return {str(key): dump(item) for key, item in value.items()}
    return value


# --- Loading ---------------------------------------------------------------

def load(annotation, value):
    """Rebuild a value the way `annotation` says it was shaped."""
    if value is None:
        return None

    if dataclasses.is_dataclass(annotation) and isinstance(value, dict):
        return _load_dataclass(annotation, value)

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (types.UnionType, typing.Union):
        wanted = [arg for arg in args if arg is not type(None)]
        return load(wanted[0], value) if len(wanted) == 1 else value

    if origin in (tuple,):
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(load(args[0], item) for item in value)
        return tuple(load(arg, item) for arg, item in zip(args, value, strict=False))
    if origin in (frozenset,):
        return frozenset(load(args[0], item) for item in value) if args else frozenset(value)
    if origin in (set,):
        return {load(args[0], item) for item in value} if args else set(value)
    if origin in (list,):
        return [load(args[0], item) for item in value] if args else list(value)
    if origin in (dict,):
        return dict(value)

    return value


def _load_dataclass(cls: type, data: dict):
    """Rebuild one dataclass, checking it is the one that was stored."""
    stored = data.get("_type")
    if stored is not None and stored != cls.__name__:
        if stored not in _TYPES:
            raise ValueError(f"unknown engine type in stored state: {stored!r}")
        cls = _TYPES[stored]
    kwargs = {
        name: load(annotation, data[name])
        for name, annotation in fields_of(cls)
        if name in data
    }
    return cls(**kwargs)


# --- Decks -----------------------------------------------------------------

def dump_deck(definition: DeckDefinition) -> dict:
    """A deck, frozen as data."""
    return dump(definition)


def load_deck(data: dict) -> DeckDefinition:
    """The deck a session started with."""
    return _load_dataclass(DeckDefinition, data)


# --- Games -----------------------------------------------------------------

#: Everything on a `Game` that a replay has to reproduce. Listed rather than
#: discovered, because `Game` is a plain class and a stray attribute finding
#: its way into a stored state would be a silent correctness problem.
GAME_ZONES = ("library", "hand", "lands", "rocks", "creatures",
              "other_permanents", "graveyard", "exiled")

#: The zones that hold permanents, each a `simulation.permanent.Zone`.
BATTLEFIELD = ("lands", "rocks", "creatures", "other_permanents")

#: The form `dump_game` writes (card model, phase 4). Format 2 keeps each
#: card once, in a table, and each zone as places in it - a battlefield zone
#: with each permanent's number - so copies come back as one object and the
#: permanents as the same ones. A state without ``format`` is format 1, every
#: card written out where it lies; `load_game` still reads it, but a playtest
#: replays its actions rather than trusting it (`is_current`).
GAME_FORMAT = 2

#: Card lists on a `Game` that are not zones a card can be moved to, but that a
#: replay still has to reproduce. Optional on the way in, because a state cached
#: before engine version 3 has no `stays_tapped` - and nothing was ever held
#: tapped then, which is exactly what an empty list says. `drawn` since the
#: card model's phase 4: a state cached before it starts the list empty, as
#: every loaded game did then.
GAME_LISTS = ("stays_tapped", "drawn")

GAME_SCALARS = ("life", "turn", "mulligans", "tapped_lands", "tapped_rocks",
                "land_drop_used", "commander_casts", "mana_available",
                "first_hand_lands", "phase", "on_the_play")

#: Scalars added after states were first cached, with the value an older
#: state means by leaving them out: no second commander was ever cast.
LATER_SCALARS = {"partner_casts": 0}


def dump_game(game: Game) -> dict:
    """The whole of a game, as a cache of a replay that already happened.

    The random state travels with it. A goldfish shuffles only when it takes an
    opening hand, so in practice nothing after that consults the generator -
    but a cached state that silently reseeded would be a bug nobody could
    reproduce, and 625 integers is a cheap way not to have one.
    """
    table: list = []
    places: dict[int, int] = {}

    def place(card) -> int:
        # By object, not by equality: two equal tokens stay two objects.
        if id(card) not in places:
            places[id(card)] = len(table)
            table.append(dump(card))
        return places[id(card)]

    state = {"format": GAME_FORMAT}
    for name in GAME_ZONES:
        zone = getattr(game, name)
        if name in BATTLEFIELD:
            state[name] = [[place(card), permanent.pid]
                           for card, permanent in zip(zone, zone.permanents, strict=True)]
        else:
            state[name] = [place(card) for card in zone]
    for name in GAME_LISTS:
        state[name] = [place(card) for card in getattr(game, name)]
    state["cards"] = table
    state["pids"] = game.pids
    state.update({name: getattr(game, name) for name in GAME_SCALARS})
    state.update({name: getattr(game, name) for name in LATER_SCALARS})
    state["mana_by_color"] = dict(game.mana_by_color)
    state["log"] = list(game.log)
    state["pool"] = game.pool.by_color() if game.pool is not None else None
    # What a pool holds beside its mana (P19 R6, R7): a cached playtest that
    # dropped them would lose a Study Hall's colour or a Treasure on reload.
    if game.pool is not None:
        state["pool_converters"] = list(game.pool.converters)
        state["pool_treasures"] = list(game.pool.treasures)
        state["pool_stored"] = _stored_at(game.pool.treasures)
        # P19 R17: mana only some spells may spend, and what it would be instead.
        state["pool_restricted"] = [dump(each) for each in game.pool.restricted]
        # P19 R22: Goldspan Dragon's Treasures make one more.
        state["pool_treasure_bonus"] = game.pool.treasure_bonus
    state["treasures"] = list(game.treasures)
    # P19 R20: which of them are a permanent's kept counters, not Treasure
    # tokens; the counters, energy and emptied stores beside them.
    state["stored"] = _stored_at(game.treasures)
    state["counters"] = dict(game.counters)
    state["energy"] = game.energy
    state["emptied"] = list(game.emptied)
    # P19 R21: +1/+1 counters, and the cards that turned into their land.
    state["plus_counters"] = dict(game.plus_counters)
    state["flipped"] = list(game.flipped)
    # P19 R22: what is on which creature, and whether combat was fought.
    state["attached"] = dict(game.attached)
    state["combat_done"] = game.combat_done
    state["landers"] = game.landers
    state["arrived"] = list(game.arrived)
    state["tapped_creatures"] = list(game.tapped_creatures)
    state["chosen_colors"] = dict(game.chosen_colors)
    state["fodder_used"] = list(game.fodder_used)
    state["imprinted"] = dict(game.imprinted)
    state["pending_mana"] = list(game.pending_mana)
    state["spells_this_turn"] = game.spells_this_turn
    state["rng"] = dump(game.rng.getstate())
    return state


def _stored_at(keys: list[str]) -> list[int]:
    """Where kept counters sit among Treasure tokens (P19 R20)."""
    return [index for index, key in enumerate(keys) if isinstance(key, Stored)]


def _with_stored(keys: list, stored: list) -> list[str]:
    """Treasure keys back, the kept counters among them as ``Stored``."""
    at = {int(index) for index in stored}
    return [Stored(key) if index in at else str(key) for index, key in enumerate(keys)]


def load_game(data: dict, deck: DeckDefinition) -> Game:
    """Rebuild a game from a cached state and the deck it was played with."""
    game = Game(random.Random(), on_the_play=data["on_the_play"], deck=deck)
    # After the constructor, never before: `Game.__init__` shuffles the deck,
    # which advances the generator. Restoring the state first and building the
    # game second leaves a session whose next shuffle differs from the one it
    # would have had - invisible in a goldfish, which shuffles only for its
    # opening hand, and impossible to reproduce once it does bite.
    game.rng.setstate(_rng_state(data["rng"]))
    cards = _deck_cards(deck)
    if data.get("format") == GAME_FORMAT:
        _load_places(game, data, [_card(row, cards) for row in data["cards"]])
    else:
        for name in GAME_ZONES:
            setattr(game, name, [_card(row, cards) for row in data[name]])
        for name in GAME_LISTS:
            setattr(game, name, [_card(row, cards) for row in data.get(name, [])])
    for name in GAME_SCALARS:
        setattr(game, name, data[name])
    for name, missing in LATER_SCALARS.items():
        setattr(game, name, data.get(name, missing))
    game.mana_by_color = dict(data["mana_by_color"])
    game.log = list(data["log"])
    game.pool = None if data["pool"] is None else ManaPool(**data["pool"])
    if game.pool is not None:
        game.pool.converters = list(data.get("pool_converters", []))
        game.pool.treasures = _with_stored(data.get("pool_treasures", []),
                                           data.get("pool_stored", []))
        game.pool.restricted = [_load_dataclass(RestrictedMana, row)
                                for row in data.get("pool_restricted", [])]
        game.pool.treasure_bonus = int(data.get("pool_treasure_bonus", 0))
    game.treasures = _with_stored(data.get("treasures", []), data.get("stored", []))
    game.counters = {str(name): int(count) for name, count in data.get("counters", {}).items()}
    game.energy = int(data.get("energy", 0))
    game.emptied = [str(name) for name in data.get("emptied", [])]
    game.plus_counters = {str(name): int(count)
                          for name, count in data.get("plus_counters", {}).items()}
    game.flipped = [str(name) for name in data.get("flipped", [])]
    game.attached = {str(name): str(host) for name, host in data.get("attached", {}).items()}
    game.combat_done = bool(data.get("combat_done", False))
    game.landers = int(data.get("landers", 0))
    game.arrived = [str(name) for name in data.get("arrived", [])]
    game.tapped_creatures = [str(name) for name in data.get("tapped_creatures", [])]
    # P19 R13: Caged Sun's colour, chosen once.
    game.chosen_colors = dict(data.get("chosen_colors", {}))
    # P19 R19: creatures tapped for another card's mana; Chrome Mox's colours.
    game.fodder_used = [str(name) for name in data.get("fodder_used", [])]
    game.imprinted = dict(data.get("imprinted", {}))
    # P19 R16: mana waiting for the pool, and the spells cast this turn.
    game.pending_mana = [str(key) for key in data.get("pending_mana", [])]
    game.spells_this_turn = int(data.get("spells_this_turn", 0))
    return game


def is_current(data: dict) -> bool:
    """Whether a cached state is in the form `dump_game` writes today."""
    return data.get("format") == GAME_FORMAT


def _load_places(game: Game, data: dict, table: list) -> None:
    """Format 2: each zone from its places in the card table, each permanent
    with its number."""
    for name in GAME_ZONES:
        if name in BATTLEFIELD:
            rows = data[name]
            setattr(game, name, [table[place] for place, _pid in rows])
            for permanent, (_place, pid) in zip(getattr(game, name).permanents, rows,
                                                 strict=True):
                permanent.pid = int(pid)
        else:
            setattr(game, name, [table[place] for place in data[name]])
    for name in GAME_LISTS:
        setattr(game, name, [table[place] for place in data.get(name, [])])
    game.pids = int(data["pids"])


def _deck_cards(deck: DeckDefinition) -> dict:
    """The deck's own card objects, each by itself: the commanders first."""
    cards = {}
    for card in (*deck.commanders, *deck.library):
        cards.setdefault(card, card)
    return cards


def _card(row: dict, cards: dict) -> Card:
    """A stored card as the deck's own object when the deck holds one equal
    to it, and as a new one otherwise (a token the game made).

    The engine asks "is this the commander" by identity, and all copies of a
    card are one object; a restored game of new objects lost both, so its
    commander no longer counted as the commander (card model, phase 4).
    """
    card = _load_dataclass(Card, row)
    return cards.get(card, card)


def _rng_state(stored):
    """`random.Random.getstate` round-tripped through JSON's lists."""
    version, internal, gauss = stored
    return (version, tuple(internal), gauss)
