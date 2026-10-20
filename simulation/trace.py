"""A game as one canonical record per turn, and digests of those records.

Phase 4 of the card model rebuild moves the engine onto blocks one family at a
time, and every step has to leave every game exactly as it was. Comparing the
aggregated numbers of a run cannot show that: two engines can agree on every
mean and still play different games. A trace can. It is the whole state a later
turn depends on, written out the same way every time, so two engines that play
the same game write the same trace - and the first turn where they part says
where to look.

The record holds names, counts and log lines, never card objects, so that it
reads the same whichever way the engine keeps its permanents. The library is a
digest of its order: a whole library per turn would make the record ten times
longer and say nothing the digest does not.

**Nothing here imports Django**, like the rest of :mod:`simulation`.
"""

import hashlib
import json

from simulation.mana import Stored

#: The zones written card by card, in the engine's own order.
ZONES = ("hand", "lands", "rocks", "creatures", "other_permanents",
         "graveyard", "exiled")

#: Game attributes written as they are: numbers, flags and lists of strings.
SCALARS = ("turn", "life", "mulligans", "tapped_lands", "tapped_rocks",
           "land_drop_used", "commander_casts", "partner_casts",
           "mana_available", "spells_this_turn", "combat_done", "landers",
           "energy")

#: The ten attributes that keep a permanent's state by its number, written
#: by the permanent's card name, so the record reads the same however the
#: engine numbers its permanents.
BY_NAME = ("arrived", "tapped_creatures", "chosen_colors", "fodder_used",
           "imprinted", "counters", "emptied", "plus_counters", "flipped",
           "attached")

#: Hex characters of a digest: a turn's, and a whole game's.
TURN_DIGEST = 8
GAME_DIGEST = 16


def _digest(value, length: int) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def _names(cards) -> list[str]:
    return [card.name for card in cards]


def _treasures(keys) -> list[str]:
    # A permanent's kept counters (`Stored`) and a Treasure token make the
    # same mana; the record tells them apart, as the game does.
    return [f"stored:{key}" if isinstance(key, Stored) else str(key) for key in keys]


def named(game, attribute: str):
    """One of the ten marks with each permanent's number written as its card
    name - what is attached to what by name on both sides."""
    names = game.pid_names

    def name(pid) -> str:
        return names.get(pid, str(pid))

    value = getattr(game, attribute)
    if isinstance(value, dict):
        return dict(sorted((name(pid), name(item) if attribute == "attached" else item)
                           for pid, item in value.items()))
    return [name(pid) for pid in value]


def record(game, logged: int = 0) -> dict:
    """The state of ``game`` now, and the log lines from ``logged`` on."""
    state = {name: _names(getattr(game, name)) for name in ZONES}
    state["library"] = _digest(_names(game.library), TURN_DIGEST)
    state["library_size"] = len(game.library)
    # A set: which ones are held, not in what order.
    state["stays_tapped"] = sorted(_names(game.stays_tapped))
    state.update({name: getattr(game, name) for name in SCALARS})
    state.update({name: named(game, name) for name in BY_NAME})
    state["treasures"] = _treasures(game.treasures)
    state["pending_mana"] = [str(key) for key in game.pending_mana]
    state["mana_by_color"] = dict(sorted(game.mana_by_color.items()))
    state["drawn"] = _names(game.drawn)
    state["pool"] = None if game.pool is None else {
        "mana": game.pool.by_color(),
        "treasures": _treasures(game.pool.treasures),
        "converters": list(game.pool.converters),
    }
    state["rng"] = _digest(game.rng.getstate(), TURN_DIGEST)
    state["log"] = list(game.log[logged:])
    return state


def turn_digest(state: dict) -> str:
    """Eight hex characters for one turn's record."""
    return _digest(state, TURN_DIGEST)


def game_digest(records) -> str:
    """Sixteen hex characters for a whole game: every record, in order."""
    return _digest(list(records), GAME_DIGEST)


def differences(before: dict, after: dict) -> list[str]:
    """The fields two records of one turn disagree on, in the record's order."""
    keys = list(before) + [key for key in after if key not in before]
    return [key for key in keys if before.get(key) != after.get(key)]


def game_seed(deck: str, index: int) -> int:
    """The seed of game ``index`` of a corpus deck: its own, so one game that
    plays differently leaves every later game of the deck as it was."""
    from simulation.analysis import sample_seed

    return sample_seed(TRACE_SEED, index, deck)


#: The run seed every corpus game's own seed comes from.
TRACE_SEED = 20261015


def play(deck, index: int, turns: int, key: str = "") -> list[dict]:
    """Game ``index`` of ``deck``, as its records: on the play when the index
    is even, on the draw when it is odd."""
    import random

    from simulation import analysis

    records: list[dict] = []
    analysis.simulate_game(random.Random(game_seed(key or deck.name, index)),
                           on_the_play=index % 2 == 0, turns=turns, deck=deck,
                           trace=records)
    return records


def digests(records) -> dict:
    """A game as the corpus keeps it: its digest and one per record."""
    return {"game": game_digest(records), "turns": [turn_digest(state) for state in records]}


def play_restored(deck, index: int, turns: int, key: str = "") -> list[dict]:
    """Game ``index`` as `play` plays it, but saved and restored before every
    turn, as a playtest is between two requests: the same records, or a
    restored game plays on differently."""
    import random

    from simulation import agent, serial
    from simulation.game import Game

    game = Game(random.Random(game_seed(key or deck.name, index)),
                on_the_play=index % 2 == 0, deck=deck)
    game.take_opening_hand()
    records = [record(game)]
    for _ in range(turns):
        logged = len(game.log)
        game = serial.load_game(json.loads(json.dumps(serial.dump_game(game))), deck)
        agent.take_turn(game)
        records.append(record(game, logged))
    return records
