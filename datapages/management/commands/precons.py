"""manage.py precons: bring the data pages' precons up to date (P11).

    manage.py precons                 new precons and changed lists from MTGJSON
    manage.py precons --rerun         and simulate every precon again
    manage.py precons --dry-run       say what would happen, change nothing
    manage.py precons --source DIR    read MTGJSON's files from DIR
    manage.py precons --from-text deck.txt --name "…" --set ABC --released 2026-11-13
                                      one deck from a plain list, before MTGJSON has it

The runs play on the long worker; each page appears when its run is done.
The weekly beat does the first form (`datapages.tasks`).
"""

from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from datapages import mtgjson, services


class DryRun(Exception):
    """Raised to roll a dry run back."""


class Command(BaseCommand):
    help = "Import and simulate the Commander precons for the data pages (P11)."

    def add_arguments(self, parser):
        parser.add_argument("--rerun", action="store_true",
                            help="Simulate every precon again, e.g. after an engine change.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would happen and change nothing.")
        parser.add_argument("--source", type=Path,
                            help="A directory with DeckList.json, SetList.json and decks/.")
        parser.add_argument("--from-text", type=Path, metavar="FILE",
                            help="One deck from a plain list; needs --name, --set, --released.")
        parser.add_argument("--name")
        parser.add_argument("--set", dest="set_code")
        parser.add_argument("--set-name", default="")
        parser.add_argument("--released", type=date.fromisoformat)

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                lines = self._run(options)
                if options["dry_run"]:
                    raise DryRun
        except DryRun:
            self.stdout.write("Dry run: nothing was changed.")
        except mtgjson.MTGJSONError as exc:
            raise CommandError(str(exc)) from exc
        for what, precon in lines:
            self.stdout.write(f"{what:>9}  {precon}")

    def _run(self, options) -> list[tuple[str, str]]:
        if options["from_text"]:
            missing = [flag for flag, key in (("--name", "name"), ("--set", "set_code"),
                                              ("--released", "released")) if not options[key]]
            if missing:
                raise CommandError(f"--from-text needs {', '.join(missing)}")
            try:
                text = options["from_text"].read_text(encoding="utf-8")
            except OSError as exc:
                raise CommandError(str(exc)) from exc
            precon, what = services.from_text(
                text, name=options["name"], set_code=options["set_code"],
                released=options["released"], set_name=options["set_name"])
            return [(what, str(precon))]
        outcome = services.refresh(source=options["source"], rerun=options["rerun"])
        return outcome.lines
