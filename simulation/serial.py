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
import json
import random
import types
import typing

from simulation import build
from simulation.blocks import Ability, Cost, Effect, Event, Which
from simulation.cards import Card, DeckDefinition
from simulation.game import Game
from simulation.mana import ManaPool, RestrictedMana, Stored
from simulation.manacost import ManaCost
from simulation.rules.amount import Amount
from simulation.rules.condition import Condition
from simulation.rules.selector import Selector

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

    A `Card` is written as what is printed on it, its play hints and its
    blocks (``abilities``), since the decoder retired (card model phase 5).
    Before, a card was written as the fields it was made with; `load` still
    reads that form (`legacy_fields_of`), so a deck a playtest stored before
    comes back as the same card.
    """
    found = _FIELDS.get(cls)
    if found is None:
        found = [(item.name, item.type) for item in dataclasses.fields(cls)]
        _FIELDS[cls] = found
    return found


_FIELDS: dict[type, list] = {}


def legacy_fields_of() -> list[tuple[str, object]]:
    """The fields a card was written with before its blocks were (phase 5):
    the printed ones and the ones it was made with (`build.MadeWith`)."""
    return ([(item.name, item.type) for item in dataclasses.fields(Card)
             if item.name != "abilities"]
            + [(item.name, item.type) for item in dataclasses.fields(build.MadeWith)])


def _discover() -> None:
    """Register every dataclass a `Card` can reach, transitively."""
    # The blocks hold atoms in fields typed `Any` - an effect's arguments, a
    # cost's selector - so those are named here; the old form's helper
    # classes are reached through `legacy_fields_of`.
    pending = [Card, DeckDefinition, Ability, Effect, Cost, Event, Which, Selector, Amount,
               Condition, ManaCost, *(found for _name, annotation in legacy_fields_of()
                                      for found in _dataclasses_in(annotation))]
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

def dump(value, annotation=None):
    """One engine value, as something `json.dumps` will accept.

    Dataclasses carry their type name so that loading knows what to rebuild.
    Tuples and frozensets become lists; the annotation on the other side says
    what they were, so the shape is not lost. Where the annotation is `Any` -
    an effect's arguments - it cannot, so a set there is written as
    ``{"_set": [...]}``.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {"_type": type(value).__name__}
        for name, field_type in fields_of(type(value)):
            out[name] = dump(getattr(value, name), field_type)
        return out
    if isinstance(value, (frozenset, set)):
        # Sorted: a set iterates in hash order, which changes from one process
        # to the next, and a dump is compared and digested.
        items = sorted((dump(item, typing.Any if annotation is typing.Any else None)
                        for item in value), key=_order)
        return {"_set": items} if annotation is typing.Any else items
    if isinstance(value, (list, tuple)):
        return [dump(item, each) for item, each in zip(value, _item_types(annotation, value),
                                                       strict=True)]
    if isinstance(value, dict):
        return {str(key): dump(item) for key, item in value.items()}
    return value


def _order(item) -> str:
    return json.dumps(item, sort_keys=True)


def _item_types(annotation, value) -> list:
    """The annotation of each item of a tuple or list."""
    if annotation is typing.Any:
        return [typing.Any] * len(value)
    args = typing.get_args(annotation)
    if typing.get_origin(annotation) in (types.UnionType, typing.Union):
        wanted = [arg for arg in args if arg is not type(None)]
        return _item_types(wanted[0], value) if len(wanted) == 1 else [None] * len(value)
    if len(args) == 2 and args[1] is Ellipsis:
        return [args[0]] * len(value)
    if args and typing.get_origin(annotation) is tuple and len(args) == len(value):
        return list(args)
    if args and typing.get_origin(annotation) is list:
        return [args[0]] * len(value)
    return [None] * len(value)


# --- Loading ---------------------------------------------------------------

def load(annotation, value):
    """Rebuild a value the way `annotation` says it was shaped."""
    if value is None:
        return None

    if annotation is typing.Any:
        return _load_any(value)
    if isinstance(value, dict) and "_set" in value:
        value = value["_set"]

    if dataclasses.is_dataclass(annotation) and isinstance(value, dict):
        return _load_dataclass(annotation, value)
    if isinstance(value, dict) and "_type" in value:
        # An annotation that names its class by a string (a `Selector`'s
        # alternatives): the dump says which.
        return _load_any(value)

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


def _load_any(value):
    """A value whose annotation is `Any`, by its own shape: a dataclass by its
    ``_type``, a set by ``_set``, a list as a tuple."""
    if isinstance(value, dict):
        if "_set" in value:
            return frozenset(_load_any(item) for item in value["_set"])
        if "_type" in value:
            if value["_type"] not in _TYPES:
                raise ValueError(f"unknown engine type in stored state: {value['_type']!r}")
            return _load_dataclass(_TYPES[value["_type"]], value)
        return value
    if isinstance(value, list):
        return tuple(_load_any(item) for item in value)
    return value


def _load_dataclass(cls: type, data: dict):
    """Rebuild one dataclass, checking it is the one that was stored."""
    stored = data.get("_type")
    if stored is not None and stored != cls.__name__:
        if stored not in _TYPES:
            raise ValueError(f"unknown engine type in stored state: {stored!r}")
        cls = _TYPES[stored]
    if cls is Card and "abilities" not in data:
        return _legacy_card(data)
    kwargs = {
        name: load(annotation, data[name])
        for name, annotation in fields_of(cls)
        if name in data
    }
    return cls(**kwargs)


def legacy_default() -> dict:
    """The old form of a card with every field at its default, the way a lean
    record of that form (a frozen reading, a snapshot before phase 5) is
    filled back in."""
    if not _LEGACY_DEFAULT:
        # The card nobody named that such a record left out: a land.
        nobody = Card(name="", mv=0, pips=0, generic=0, kind="land")
        made_with = build.MadeWith()
        for name, annotation in legacy_fields_of():
            value = getattr(made_with if name in build.FIELDS else nobody, name)
            _LEGACY_DEFAULT[name] = dump(value, annotation)
        _LEGACY_DEFAULT["_type"] = "Card"
    return _LEGACY_DEFAULT


_LEGACY_DEFAULT: dict = {}


def _legacy_card(data: dict) -> Card:
    """A card written before its blocks were: made again with the fields it
    was made with (`legacy_fields_of`), which `build.card` turns into its
    blocks."""
    return build.card(**{name: load(annotation, data[name])
                         for name, annotation in legacy_fields_of() if name in data})


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

#: The form `dump_game` writes (card model, phase 4). Each card object once,
#: in a table, and each zone as places in it - a battlefield zone with each
#: permanent's number - so every copy comes back as an object of its own and
#: the permanents as the same ones. Format 3 (PR 17) keeps every mark by the
#: permanent's number instead of the card's name. `load_game` reads only this
#: format: a playtest replays its actions from an older state (`is_current`).
GAME_FORMAT = 4

#: Card lists on a `Game` that are not zones a card can be moved to, but that a
#: replay still has to reproduce.
GAME_LISTS = ("drawn",)

GAME_SCALARS = ("life", "turn", "mulligans", "land_drop_used", "commander_casts",
                "partner_casts", "mana_available", "first_hand_lands", "phase", "on_the_play")


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
            state[name] = [[place(card), permanent.pid, permanent.tapped, permanent.held]
                           for card, permanent in zip(zone, zone.permanents, strict=True)]
        else:
            state[name] = [place(card) for card in zone]
    for name in GAME_LISTS:
        state[name] = [place(card) for card in getattr(game, name)]
    state["cards"] = table
    state["pids"] = game.pids
    state["pid_names"] = dict(game.pid_names)
    state.update({name: getattr(game, name) for name in GAME_SCALARS})
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
    if not is_current(data):
        raise ValueError("a state in an older format: replay its actions instead")
    game.rng.setstate(_rng_state(data["rng"]))
    cards, used = _deck_cards(deck), set()
    _load_places(game, data, [_card(row, cards, used) for row in data["cards"]])
    for name in GAME_SCALARS:
        setattr(game, name, data[name])
    game.mana_by_color = dict(data["mana_by_color"])
    game.log = list(data["log"])
    game.pool = None if data["pool"] is None else ManaPool(**data["pool"])
    if game.pool is not None:
        game.pool.converters = list(data["pool_converters"])
        game.pool.treasures = _with_stored(data["pool_treasures"], data["pool_stored"])
        game.pool.restricted = [_load_dataclass(RestrictedMana, row)
                                for row in data["pool_restricted"]]
        game.pool.treasure_bonus = int(data["pool_treasure_bonus"])
    game.treasures = _with_stored(data["treasures"], data["stored"])
    game.counters = {int(pid): int(count) for pid, count in data["counters"].items()}
    game.energy = int(data["energy"])
    game.emptied = [int(pid) for pid in data["emptied"]]
    game.plus_counters = {int(pid): int(count) for pid, count in data["plus_counters"].items()}
    game.flipped = [int(pid) for pid in data["flipped"]]
    game.attached = {int(pid): int(host) for pid, host in data["attached"].items()}
    game.combat_done = bool(data["combat_done"])
    game.landers = int(data["landers"])
    game.arrived = [int(pid) for pid in data["arrived"]]
    game.tapped_creatures = [int(pid) for pid in data["tapped_creatures"]]
    # P19 R13: Caged Sun's colour, chosen once.
    game.chosen_colors = {int(pid): color for pid, color in data["chosen_colors"].items()}
    # P19 R19: creatures tapped for another card's mana; Chrome Mox's colours.
    game.fodder_used = [int(pid) for pid in data["fodder_used"]]
    game.imprinted = {int(pid): colors for pid, colors in data["imprinted"].items()}
    # P19 R16: mana waiting for the pool, and the spells cast this turn.
    game.pending_mana = [str(key) for key in data["pending_mana"]]
    game.spells_this_turn = int(data["spells_this_turn"])
    return game


def is_current(data: dict) -> bool:
    """Whether a cached state is in the form `dump_game` writes today."""
    return data.get("format") == GAME_FORMAT


def _load_places(game: Game, data: dict, table: list) -> None:
    """Each zone from its places in the card table, each permanent with its
    number and whether it is tapped or held."""
    for name in GAME_ZONES:
        if name in BATTLEFIELD:
            rows = data[name]
            setattr(game, name, [table[row[0]] for row in rows])
            for permanent, (_place, pid, tapped, held) in zip(getattr(game, name).permanents,
                                                              rows, strict=True):
                permanent.pid, permanent.tapped, permanent.held = int(pid), tapped, held
        else:
            setattr(game, name, [table[place] for place in data[name]])
    for name in GAME_LISTS:
        setattr(game, name, [table[place] for place in data[name]])
    game.pids = int(data["pids"])
    game.pid_names = {int(pid): name for pid, name in data["pid_names"].items()}


def _deck_cards(deck: DeckDefinition) -> dict:
    """The deck's own card objects, each by itself: the commanders first."""
    cards = {}
    for card in (*deck.commanders, *deck.library):
        cards.setdefault(card, card)
    return cards


def _card(row: dict, cards: dict, used: set) -> Card:
    """A stored card as the deck's own object the first time the deck holds
    one equal to it, and as a new one otherwise (another copy, a token).

    The engine asks "is this the commander" by identity, so a restored
    commander has to be the deck's; and each copy is an object of its own, so
    a second copy may not become the first (card model, phase 4).
    """
    card = _load_dataclass(Card, row)
    own = cards.get(card)
    if own is None or id(own) in used:
        return card
    used.add(id(own))
    return own


def _rng_state(stored):
    """`random.Random.getstate` round-tripped through JSON's lists."""
    version, internal, gauss = stored
    return (version, tuple(internal), gauss)
