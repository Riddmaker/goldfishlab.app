"""The gate corpus of the card model rebuild, phase 4: do games stay the same?

    manage.py engine_games --build      # draw the corpus once (.data/game_corpus_v1.json)
    manage.py engine_games --freeze     # play it and keep what was played
    manage.py engine_games --diff       # play it again and name what moved

`--build` reads the newest frozen card reading (`engine_coverage --freeze`).
`--freeze` and `--diff` compare two engines on one database: make the frozen
file again from a worktree of the last merged main whenever the database
changes. See `simulations/games.py`; the speed of two engines is compared by
`scripts/bench_engine.py --baseline`.
"""

import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from simulations import differential, games


class Command(BaseCommand):
    help = "Play the gate corpus and compare its games turn by turn (card model, phase 4)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--build", action="store_true")
        group.add_argument("--freeze", action="store_true")
        group.add_argument("--diff", action="store_true")
        parser.add_argument("--frozen", help="the frozen file (default: the newest in .data/)")
        parser.add_argument("--games", type=int, default=games.GAMES)
        parser.add_argument("--processes", type=int, default=games.PROCESSES)
        parser.add_argument("--limit", type=int, default=30, help="decks listed in the diff")

    def handle(self, *args, **options):
        if options["build"]:
            reading = differential.latest_frozen()
            if reading is None:
                raise CommandError("nothing frozen yet: run engine_coverage --freeze first")
            corpus = games.build_corpus(reading)
            drawn = sum(1 for deck in corpus["decks"] if deck["source"] == "drawn")
            self.stdout.write(f"{len(corpus['decks'])} decks ({drawn} drawn) written to "
                              f"{games.CORPUS}; features below {games.COVER} decks: "
                              f"{len(corpus['uncovered'])}")
            return
        if options["freeze"]:
            path = Path(options["frozen"]) if options["frozen"] else games.frozen_path()
            started = time.monotonic()
            count = games.freeze(path, games=options["games"], processes=options["processes"])
            self.stdout.write(f"{count} games frozen into {path} "
                              f"in {time.monotonic() - started:.0f} s")
            return
        path = Path(options["frozen"]) if options["frozen"] else games.latest_frozen()
        if path is None or not path.exists():
            raise CommandError("nothing frozen yet: run engine_games --freeze first")
        moved = games.diff(path, processes=options["processes"])
        for line in games.lines(moved, options["limit"]):
            self.stdout.write(line)
        if any(deck.games for deck in moved):
            raise CommandError(f"games differ in {sum(1 for deck in moved if deck.games)} "
                               f"deck(s) against {path.name}")
        self.stdout.write(f"every game plays as in {path.name}"
                          + (" (some cards read differently)" if moved else ""))

