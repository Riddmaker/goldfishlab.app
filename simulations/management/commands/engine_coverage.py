"""How much the engine reads, on today's catalogue (P19).

    manage.py engine_coverage                  # the numbers: catalogue and decks
    manage.py engine_coverage --check          # today's catalogue against the snapshot
    manage.py engine_coverage --export-fixture # rebuild the fixed set (rarely)

The gate itself is `tests/test_engine_coverage.py`, on the committed fixture;
`COVERAGE_WRITE=1` makes it write the snapshots instead of comparing. This
command looks at a development database, whose Scryfall tags drift from the
fixture's - so `--check` also shows what new tags changed.
`--export-fixture` needs the full catalogue and the precons
(`ingest_scryfall`, `precons`); see `simulations/coverage.py`.
"""

from django.core.management.base import BaseCommand, CommandError

from simulations import coverage


class Command(BaseCommand):
    help = "Engine coverage on today's catalogue: the numbers and the snapshot check (P19)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--check", action="store_true")
        group.add_argument("--export-fixture", action="store_true")

    def handle(self, *args, **options):
        if options["export_fixture"]:
            count = coverage.export_fixture(coverage.fixed_set())
            self.stdout.write(f"{count} cards written to {coverage.CARDS_FIXTURE}")
            count = coverage.export_decks()
            self.stdout.write(f"{count} decks written to {coverage.DECKS_FIXTURE}")
            self.stdout.write("now: COVERAGE_WRITE=1 pytest tests/test_engine_coverage.py")
            return

        if options["check"]:
            from cards.models import OracleCard

            before = coverage.load_snapshot()
            after = coverage.snapshot(
                OracleCard.objects.filter(pk__in=coverage.ids(before)).select_related("profile")
            )
            diff = coverage.compare(before, after)
            for line in diff.lines(before, after):
                self.stdout.write(line)
            if diff.lost:
                raise CommandError(f"{len(diff.lost)} card(s) read before and not now")
            self.stdout.write("every card reads as in the snapshot" if diff.empty else
                              "no card reads worse than in the snapshot")
            return

        for line in coverage.report():
            self.stdout.write(line)
