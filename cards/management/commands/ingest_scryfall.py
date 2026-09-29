"""Load the Scryfall bulk files into the local catalogue.

    py manage.py ingest_scryfall                       # cards + tags, skip if unchanged
    py manage.py ingest_scryfall --kind oracle_tags    # just the tags
    py manage.py ingest_scryfall --kind default_cards  # the 78.8 MB printings file
    py manage.py ingest_scryfall --force --measure     # rebuild, report memory
    py manage.py ingest_scryfall --source path.jsonl.gz --kind oracle_cards

Order matters: taggings and printings both carry a foreign key to the card, so
cards go first and a row whose card is missing is counted as skipped rather
than crashing the run.

**`--kind all` deliberately does not include `default_cards`.** This command is
in the boot path and in the README's first commands, and a routine invocation
should not pull 78.8 MB because somebody upgraded. Printings are asked for by
name; everything that reads them works without them.

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

from cards import ingest, profiles
from cards.models import BulkImport, DerivedProfile, OracleCard
from cards.scryfall import ScryfallError
from decks.models import Deck
from decks.services import recount_later

#: The kinds whose rows a profile is derived from. Printings are not one of
#: them - a profile is about a card, not about which Swamp somebody owns.
PROFILE_INPUTS = {BulkImport.Kind.ORACLE_CARDS, BulkImport.Kind.ORACLE_TAGS}

#: What `--kind all` means: the catalogue, and nothing that is 78.8 MB.
KINDS = [BulkImport.Kind.ORACLE_CARDS, BulkImport.Kind.ORACLE_TAGS]

#: Everything that can be asked for by name, in dependency order.
ALL_KINDS = [
    BulkImport.Kind.ORACLE_CARDS,
    BulkImport.Kind.DEFAULT_CARDS,
    BulkImport.Kind.ORACLE_TAGS,
]


class Command(BaseCommand):
    help = "Ingest Scryfall oracle_cards and oracle_tags bulk data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=[*ALL_KINDS, "all"],
            default="all",
            help=(
                "Which bulk file to load. Default 'all' means cards then tags; "
                "default_cards (printings, 78.8 MB) must be named explicitly."
            ),
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
            self._rebuild_profiles("asked for")
            return

        kinds = KINDS if options["kind"] == "all" else [options["kind"]]

        if options["source"] and len(kinds) > 1:
            raise CommandError("--source needs an explicit --kind")

        changed = False
        for kind in kinds:
            self.stdout.write(f"{kind}: checking...")
            try:
                result = self._ingest(kind, options)
            except ScryfallError as exc:
                raise CommandError(str(exc)) from exc

            style = self.style.WARNING if result.skipped else self.style.SUCCESS
            self.stdout.write(style(f"  {result}"))
            changed |= kind in PROFILE_INPUTS and not result.skipped

        missing = OracleCard.objects.count() - DerivedProfile.objects.count()
        if changed:
            self._rebuild_profiles("cards or tags changed")
        elif missing:
            self._rebuild_profiles(f"{missing} cards had no profile")

    def _rebuild_profiles(self, reason: str) -> None:
        self.stdout.write(f"profiles: rebuilding ({reason})...")
        written = profiles.rebuild()
        # A profile is what decides which cards the engine can read, so every
        # deck's red marker has to be counted again (Phase 9 C2).
        recount_later(Deck.objects.all())
        self.stdout.write(self.style.SUCCESS(f"  {written} profiles derived"))

    def _ingest(self, kind: str, options) -> ingest.IngestResult:
        common = {
            "source": options["source"],
            "force": options["force"],
            "measure": options["measure"],
        }
        if kind == BulkImport.Kind.ORACLE_CARDS:
            return ingest.ingest_cards(limit=options["limit"], **common)
        if kind == BulkImport.Kind.DEFAULT_CARDS:
            return ingest.ingest_printings(limit=options["limit"], **common)
        return ingest.ingest_tags(**common)
