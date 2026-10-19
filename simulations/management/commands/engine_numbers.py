"""The numbers of the database decks, kept to compare two engines (card model, phase 4).

    manage.py engine_numbers --save     # .data/numbers_v<engine>.json
    manage.py engine_numbers --diff     # today's numbers against the newest saved ones

Every deck of the development database (the gate corpus's database decks)
plays `games.NUMBERS_GAMES` games of eight turns through `analysis.run` with
one seed; the diff names every number that moved, as the coverage gate does.
Mainly the baseline for gate E (PR 17), where numbers may move with a cause.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from simulations import coverage, games


class Command(BaseCommand):
    help = "Save or compare the numbers of the database decks (card model, phase 4)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--save", action="store_true")
        group.add_argument("--diff", action="store_true")
        parser.add_argument("--file", help="the numbers file (default: by engine version)")
        parser.add_argument("--games", type=int, default=games.NUMBERS_GAMES)
        parser.add_argument("--processes", type=int, default=games.PROCESSES)
        parser.add_argument("--limit", type=int, default=80)

    def handle(self, *args, **options):
        found = games.numbers(options["games"], options["processes"])
        if options["save"]:
            path = Path(options["file"]) if options["file"] else games.numbers_path()
            path.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            self.stdout.write(f"{len(found)} decks written to {path}")
            return
        path = Path(options["file"]) if options["file"] else _newest()
        if path is None or not path.exists():
            raise CommandError("nothing saved yet: run engine_numbers --save first")
        moved = coverage.compare_numbers(json.loads(path.read_text(encoding="utf-8")), found)
        for line in moved[:options["limit"]]:
            self.stdout.write(line)
        if moved:
            raise CommandError(f"{len(moved)} number(s) moved against {path.name}")
        self.stdout.write(f"every number is as in {path.name}")


def _newest() -> Path | None:
    found = sorted(games.DATA.glob("numbers_v*.json"),
                   key=lambda path: int(path.stem.split("_v")[1]))
    return found[-1] if found else None
