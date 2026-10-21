"""Card model phase 0.8: is code compiled from blocks as fast as today's?

    .venv/bin/python scripts/bench_closures.py

Two hot paths of a goldfish game, each played twice - by today's engine code
and by a plain Python closure compiled once per card from its blocks
(`simulation.blocks`), never `eval`:

* **mana**: ten sources tap into a fresh pool, the way a payment opens one;
* **trigger**: a land enters a board of fifteen permanents, two of which
  have a landfall trigger (mana, a Treasure).

Both sides must leave the same state, which is asserted. Timings are the
median of five interleaved runs, so a busy machine slows both alike. Not part
of the test suite: timings there would flake.
"""

import random
import statistics
import sys
import timeit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulation import (
    blocks,  # noqa: E402
    build,
)
from simulation.cards import FLAT, LAND, LANDFALL, Card, ManaAbility, Trigger  # noqa: E402
from simulation.fixtures import chainer  # noqa: E402
from simulation.game import Game  # noqa: E402
from simulation.mana import ManaPool, _add_produced, plain_ability  # noqa: E402

RUNS = 5
NUMBER = 20_000


def _land(name, color, **extra):
    return build.card(name=name, mv=0, pips=0, generic=0, kind=LAND,
                      mana_abilities=(ManaAbility(FLAT, ((color, 1),)),), **extra)


SOURCES = [
    _land("Forest", "G"), _land("Island", "U"), _land("Swamp", "B"),
    _land("Mountain", "R"), _land("Plains", "W"), _land("Forest 2", "G"),
    build.card(name="Sol Ring", mv=1, pips=0, generic=1, kind="rock",
               mana_abilities=(ManaAbility(FLAT, (("C", 2),)),)),
    build.card(name="Talisman", mv=2, pips=0, generic=2, kind="rock",
               mana_abilities=(ManaAbility(FLAT, (("U", 1),)), ManaAbility(FLAT, (("B", 1),)))),
    build.card(name="Elf", mv=1, pips=1, generic=0, kind="creature",
               mana_abilities=(ManaAbility(FLAT, (("G", 1),)),)),
    build.card(name="Monolith", mv=3, pips=0, generic=3, kind="rock",
               mana_abilities=(ManaAbility(FLAT, (("C", 3),)),)),
]


# --- the compiler: blocks -> closures ---------------------------------------------


def _compile_add_mana(effect):
    produces = effect.arg("produces")
    if len(produces) == 1:
        (color, amount), = produces

        def add(pool):
            pool.add(color, amount)
        return add

    def add_all(pool):
        for color, amount in produces:
            pool.add(color, amount)
    return add_all


def compile_tap(card):
    """The card's first plain mana ability, as one call into the pool."""
    for ability in card.abilities:
        effect = ability.effects[0]
        if (ability.kind == blocks.MANA and effect.verb == "add_mana"
                and effect.arg("rule") == FLAT and not ability.conds
                and not effect.arg("spend_only")):
            return _compile_add_mana(effect)
    return None


def compile_triggers(card):
    """The card's triggered abilities, by event: a closure per ability."""
    found: dict[str, list] = {}
    for ability in card.abilities:
        if ability.kind != blocks.TRIGGERED or ability.form != "trigger":
            continue
        steps = []
        made = []
        for effect in ability.effects:
            if effect.verb == "add_mana":
                color, amount = effect.arg("color"), effect.arg("n")
                steps.append(lambda game, c=color, n=amount: game.pending_mana.extend([c] * n))
                made.append(f"+{amount}{color}")
            elif effect.verb == "create_token":
                mana, count = effect.arg("mana"), effect.arg("n")
                steps.append(lambda game, m=mana, n=count: game._add_treasures(m, n))
                made.append(f"{count} Treasure")

        # The log line is built once, here; today's code builds it each time.
        line = f"  -> {card.name}: {', '.join(made)}"

        def run(game, steps=tuple(steps), line=line):
            for step in steps:
                step(game)
            game.note(line)
        found.setdefault(ability.event.name, []).append(run)
    return found


# --- the two paths -------------------------------------------------------------------

COMPILED_TAPS = {id(card): compile_tap(card) for card in SOURCES}


def tap_today():
    pool = ManaPool()
    for card in SOURCES:
        _add_produced(pool, plain_ability(card))
    return pool


def tap_compiled():
    pool = ManaPool()
    for card in SOURCES:
        COMPILED_TAPS[id(card)](pool)
    return pool


COBRA = build.card(name="Cobra", mv=3, pips=1, generic=2, kind="creature",
                   triggers=(Trigger(LANDFALL, mana=1, mana_color="G"),))
TITHE = build.card(name="Tithe", mv=4, pips=1, generic=3, kind="enchantment",
                   triggers=(Trigger(LANDFALL, treasures=1, treasure_mana="C"),))
BOARD = [*SOURCES, COBRA, TITHE,
         Card(name="Bear", mv=2, pips=1, generic=1, kind="creature"),
         Card(name="Wall", mv=1, pips=1, generic=0, kind="creature"),
         Card(name="Rug", mv=2, pips=0, generic=2, kind="enchantment")]
COMPILED_TRIGGERS = {id(card): compile_triggers(card) for card in BOARD}
LAND_IN = _land("Fresh Forest", "G")


def _game() -> Game:
    game = Game(random.Random(1), deck=chainer.DECK)  # noqa: S311 - a seeded game
    game.lands = [card for card in BOARD if card.kind == LAND]
    game.rocks = [card for card in BOARD if card.kind == "rock"]
    game.creatures = [card for card in BOARD if card.kind == "creature"]
    game.other_permanents = [card for card in BOARD if card.kind == "enchantment"]
    game.pending_mana = []
    return game


GAME = _game()


def fire_today():
    GAME.pending_mana.clear()
    GAME.treasures.clear()
    GAME.log.clear()
    GAME.fire(LANDFALL, LAND_IN)


def fire_compiled():
    GAME.pending_mana.clear()
    GAME.treasures.clear()
    GAME.log.clear()
    sources = sorted((card for card in GAME.battlefield
                      if LANDFALL in COMPILED_TRIGGERS.get(id(card), {})),
                     key=lambda card: (-card.mv, card.name))
    for card in sources:
        for run in COMPILED_TRIGGERS[id(card)][LANDFALL]:
            run(GAME)


def _same_state():
    if tap_today() != tap_compiled():
        raise SystemExit("mana: the compiled closures make other mana")
    fire_today()
    today = (list(GAME.pending_mana), list(GAME.treasures), list(GAME.log))
    fire_compiled()
    if today != (list(GAME.pending_mana), list(GAME.treasures), list(GAME.log)):
        raise SystemExit(f"trigger: the compiled closures do otherwise: {today}")


def main() -> None:
    _same_state()
    for name, today, compiled in (("mana", tap_today, tap_compiled),
                                  ("trigger", fire_today, fire_compiled)):
        old, new = [], []
        for _ in range(RUNS):
            old.append(timeit.timeit(today, number=NUMBER))
            new.append(timeit.timeit(compiled, number=NUMBER))
        old_us = statistics.median(old) / NUMBER * 1e6
        new_us = statistics.median(new) / NUMBER * 1e6
        print(f"{name:8s} today {old_us:6.2f} us   compiled {new_us:6.2f} us   "
              f"compiled/today {new_us / old_us:5.2f}")


if __name__ == "__main__":
    main()
