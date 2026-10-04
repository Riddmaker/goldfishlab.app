"""Load the Scryfall bulk files into the local catalogue.

    py manage.py ingest_scryfall                       # cards + tags, skip if unchanged
    py manage.py ingest_scryfall --kind oracle_tags    # just the tags
    py manage.py ingest_scryfall --force --measure     # rebuild, report memory
    py manage.py ingest_scryfall --source path.jsonl.gz --kind oracle_cards

Order matters: a tagging carries a foreign key to the card, so cards go first
and a row whose card is missing is counted as skipped rather than crashing the
run. (The 78.8 MB `default_cards` printings file went in phase 9 I - only the
collection's prices read it.)

**It also derives the card profiles, and that is not optional.** The engine
reads `DerivedProfile`, never the raw card: a catalogue with cards and no
profiles simulates every card as a colourless artifact and every deck as one
with no lands, while every deck page still looks perfectly healthy. Until the
2026-09-25 review nothing outside the test suite ever called
`profiles.rebuild()`, so a fresh production database would have been exactly
that. Profiles are rebuilt whenever cards or tags changed, whenever any card is
missing its profile, and on `--profiles`, which is what a deploy that changed
the deriver but not the bulk files needs.
"""

from django.core.management.base import BaseCommand, CommandError

from cards import refresh
from cards.scryfall import ScryfallError

KINDS = refresh.KINDS


class Command(BaseCommand):
    help = "Ingest Scryfall oracle_cards and oracle_tags bulk data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=[*KINDS, "all"],
            default="all",
            help="Which bulk file to load. Default 'all' means cards then tags.",
        )
        parser.add_argument(
            "--source",
            help="Local .jsonl.gz path instead of downloading. Requires --kind.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-import even when this version was already loaded.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Stop after N rows. Cards only; for smoke tests.",
        )
        parser.add_argument(
            "--measure",
            action="store_true",
            help="Report peak memory. Costs roughly 2x runtime.",
        )
        parser.add_argument(
            "--profiles",
            action="store_true",
            help=(
                "Only re-derive the card profiles, downloading nothing. Needed "
                "after a deploy that changed cards/profiles.py."
            ),
        )

    def handle(self, *args, **options):
        if options["profiles"]:
            self._profiles("asked for")
            self._derived(refresh.rebuild_profiles())
            return

        kinds = KINDS if options["kind"] == "all" else [options["kind"]]

        if options["source"] and len(kinds) > 1:
            raise CommandError("--source needs an explicit --kind")

        # The work itself is shared with the nightly task (cards/refresh.py).
        try:
            loaded = refresh.load(
                kinds,
                source=options["source"],
                force=options["force"],
                limit=options["limit"],
                measure=options["measure"],
                on_result=self._result,
                on_profiles=self._profiles,
            )
        except ScryfallError as exc:
            raise CommandError(str(exc)) from exc

        if loaded.profiles_reason:
            self._derived(loaded.profiles_written)

    def _result(self, result) -> None:
        style = self.style.WARNING if result.skipped else self.style.SUCCESS
        self.stdout.write(style(f"  {result}"))

    def _profiles(self, reason: str) -> None:
        self.stdout.write(f"profiles: rebuilding ({reason})...")

    def _derived(self, written: int) -> None:
        self.stdout.write(self.style.SUCCESS(f"  {written} profiles derived"))
