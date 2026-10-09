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
from simulation.mana import ManaPool

#: Dataclasses reachable from a deck, by name. Loading consults this rather
#: than anything importable, so a crafted payload cannot name an arbitrary
#: class - the game state comes back out of a database we wrote, but it is
#: still input, and input picks from a list.
_TYPES: dict[str, type] = {}


def _register(cls: type) -> type:
    _TYPES[cls.__name__] = cls
    return cls


def _discover() -> None:
    """Register every dataclass a `Card` can reach, transitively."""
    pending = [Card, DeckDefinition]
    while pending:
        cls = pending.pop()
        if cls.__name__ in _TYPES:
            continue
        _register(cls)
        for field in dataclasses.fields(cls):
            for found in _dataclasses_in(field.type):
                pending.append(found)


def _dataclasses_in(annotation) -> list[type]:
    """Every dataclass mentioned by a type annotation, however nested."""
    if dataclasses.is_dataclass(annotation):
        return [annotation]
    return [found
            for arg in typing.get_args(annotation)
            for found in _dataclasses_in(arg)]


_discover()


# --- Dumping ---------------------------------------------------------------

def dump(value):
    """One engine value, as something `json.dumps` will accept.

    Dataclasses carry their type name so that loading knows what to rebuild.
    Tuples and frozensets become lists; the annotation on the other side says
    what they were, so the shape is not lost.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {"_type": type(value).__name__}
        for field in dataclasses.fields(value):
            out[field.name] = dump(getattr(value, field.name))
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
        field.name: load(field.type, data[field.name])
        for field in dataclasses.fields(cls)
        if field.name in data
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

#: Card lists on a `Game` that are not zones a card can be moved to, but that a
#: replay still has to reproduce. Optional on the way in, because a state cached
#: before engine version 3 has no `stays_tapped` - and nothing was ever held
#: tapped then, which is exactly what an empty list says.
GAME_LISTS = ("stays_tapped",)

GAME_SCALARS = ("life", "turn", "mulligans", "tapped_lands", "tapped_rocks",
                "land_drop_used", "commander_casts", "mana_available",
                "first_hand_lands", "phase", "on_the_play")


def dump_game(game: Game) -> dict:
    """The whole of a game, as a cache of a replay that already happened.

    The random state travels with it. A goldfish shuffles only when it takes an
    opening hand, so in practice nothing after that consults the generator -
    but a cached state that silently reseeded would be a bug nobody could
    reproduce, and 625 integers is a cheap way not to have one.
    """
    state = {name: [dump(card) for card in getattr(game, name)]
             for name in (*GAME_ZONES, *GAME_LISTS)}
    state.update({name: getattr(game, name) for name in GAME_SCALARS})
    state["mana_by_color"] = dict(game.mana_by_color)
    state["log"] = list(game.log)
    state["pool"] = game.pool.by_color() if game.pool is not None else None
    # What a pool holds beside its mana (P19 R6, R7): a cached playtest that
    # dropped them would lose a Study Hall's colour or a Treasure on reload.
    if game.pool is not None:
        state["pool_converters"] = list(game.pool.converters)
        state["pool_treasures"] = list(game.pool.treasures)
    state["treasures"] = list(game.treasures)
    state["rng"] = dump(game.rng.getstate())
    return state


def load_game(data: dict, deck: DeckDefinition) -> Game:
    """Rebuild a game from a cached state and the deck it was played with."""
    game = Game(random.Random(), on_the_play=data["on_the_play"], deck=deck)
    # After the constructor, never before: `Game.__init__` shuffles the deck,
    # which advances the generator. Restoring the state first and building the
    # game second leaves a session whose next shuffle differs from the one it
    # would have had - invisible in a goldfish, which shuffles only for its
    # opening hand, and impossible to reproduce once it does bite.
    game.rng.setstate(_rng_state(data["rng"]))
    for name in GAME_ZONES:
        setattr(game, name, [_load_dataclass(Card, row) for row in data[name]])
    for name in GAME_LISTS:
        setattr(game, name, [_load_dataclass(Card, row) for row in data.get(name, [])])
    for name in GAME_SCALARS:
        setattr(game, name, data[name])
    game.mana_by_color = dict(data["mana_by_color"])
    game.log = list(data["log"])
    game.pool = None if data["pool"] is None else ManaPool(**data["pool"])
    if game.pool is not None:
        game.pool.converters = list(data.get("pool_converters", []))
        game.pool.treasures = list(data.get("pool_treasures", []))
    game.treasures = list(data.get("treasures", []))
    return game


def _rng_state(stored):
    """`random.Random.getstate` round-tripped through JSON's lists."""
    version, internal, gauss = stored
    return (version, tuple(internal), gauss)
