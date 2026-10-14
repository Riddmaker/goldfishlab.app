"""Export the stored card corrections for the card-model rebuild (phase 0).

    manage.py annotations_export --out corrections.json

Writes each correction's card, scope (builtin, user, deck), overrides and
patches, and how many players made the same one - **never who**: no owner,
no deck, no note. The file is read locally against the development catalogue
(`simulations.parity`) and is never committed.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from simulations import parity


class Command(BaseCommand):
    help = "Export card corrections without owners or decks, for the parity corpus."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="the JSON file to write")

    def handle(self, *args, **options):
        path = Path(options["out"])
        if path.exists():
            raise CommandError(f"{path} exists; pick another name")
        found = parity.rows()
        path.write_text(json.dumps(found, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
        scopes: dict[str, int] = {}
        for row in found:
            scopes[row["scope"]] = scopes.get(row["scope"], 0) + row["count"]
        self.stdout.write(f"{len(found)} different corrections ({scopes}) written to {path}")
