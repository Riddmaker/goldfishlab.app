"""How much the engine reads, on today's catalogue (P19).

    manage.py engine_coverage                  # the numbers: catalogue and decks
    manage.py engine_coverage --check          # today's catalogue against the snapshot
    manage.py engine_coverage --export-fixture # rebuild the fixed set (rarely)
    manage.py engine_coverage --freeze         # freeze the whole catalogue's reading
    manage.py engine_coverage --diff           # today's code against the frozen reading
    manage.py engine_coverage --diff --explain # and the cause of every card that moved
    manage.py engine_coverage --blocks         # every frozen card through blocks and back

The gate itself is `tests/test_engine_coverage.py`, on the committed fixture;
`COVERAGE_WRITE=1` makes it write the snapshots instead of comparing. This
command looks at a development database, whose Scryfall tags drift from the
fixture's - so `--check` also shows what new tags changed.
`--export-fixture` needs the full catalogue and the precons
(`ingest_scryfall`, `precons`); see `simulations/coverage.py`.
`--freeze` and `--diff` cover every Commander-legal card, for the card-model
rebuild; see `simulations/differential.py`.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from simulations import coverage, differential


class Command(BaseCommand):
    help = "Engine coverage on today's catalogue: the numbers and the snapshot check (P19)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--check", action="store_true")
        group.add_argument("--export-fixture", action="store_true")
        group.add_argument("--freeze", action="store_true")
        group.add_argument("--diff", action="store_true")
        group.add_argument("--blocks", action="store_true")
        parser.add_argument("--frozen", help="the frozen file (default: the newest in .data/)")
        parser.add_argument("--explain", action="store_true",
                            help="with --diff: give every moved card its causes, and fail "
                                 "on a card nothing explains (card model, gate B)")
        parser.add_argument("--out", help="with --explain: write each card's causes here (JSON)")
        parser.add_argument("--limit", type=int, default=30,
                            help="cards listed per group in the diff")

    def handle(self, *args, **options):
        if options["export_fixture"]:
            count = coverage.export_fixture(coverage.fixed_set())
            self.stdout.write(f"{count} cards written to {coverage.CARDS_FIXTURE}")
            count = coverage.export_decks()
            self.stdout.write(f"{count} decks written to {coverage.DECKS_FIXTURE}")
            self.stdout.write("now: COVERAGE_WRITE=1 pytest tests/test_engine_coverage.py")
            return

        if options["freeze"]:
            path = differential.frozen_path() if not options["frozen"] else Path(options["frozen"])
            count = differential.freeze(path)
            self.stdout.write(f"{count} cards frozen into {path}")
            return

        if options["diff"] and options["explain"]:
            self._explain(options)
            return
        if options["diff"]:
            self._diff(options)
            return

        if options["blocks"]:
            self._blocks(options)
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

    @staticmethod
    def _frozen(options) -> Path:
        path = Path(options["frozen"]) if options["frozen"] else differential.latest_frozen()
        if path is None or not path.exists():
            raise CommandError("nothing frozen yet: run engine_coverage --freeze first")
        return path

    def _blocks(self, options):
        found = differential.round_trip(self._frozen(options))
        self.stdout.write(f"{found['cards']} cards, abilities per card {found['abilities']}, "
                          f"{found['extra']} with a value nothing reads")
        for name, count in found["verbs"].most_common():
            self.stdout.write(f"  {count:6d}  {name}")
        if found["lost"]:
            raise CommandError(f"{len(found['lost'])} card(s) changed on the way: "
                               f"{', '.join(found['lost'][:options['limit']])}")
        self.stdout.write("every card came back the same")

    def _diff(self, options):
        path = self._frozen(options)
        diff, before, after, notes = differential.diff(path)
        for note in notes:
            self.stderr.write(note)
        for line in diff.lines(before, after, limit=options["limit"]):
            self.stdout.write(line)
        if diff.lost:
            raise CommandError(f"{len(diff.lost)} card(s) read before and not now")
        self.stdout.write(f"every card reads as in {path.name}" if diff.empty else
                          f"no card reads worse than in {path.name}")

    def _explain(self, options):
        path = self._frozen(options)
        found = differential.explain(path)
        for note in found.notes:
            self.stderr.write(note)
        diff = found.diff
        self.stdout.write(f"against {path.name}: {len(diff.lost)} lost, {len(diff.gained)} "
                          f"gained, {len(diff.changed)} read differently, "
                          f"{len(diff.reasons)} unread for other reasons")
        for cause, count in sorted(found.by_cause().items()):
            self.stdout.write(f"  {count:6d}  {cause}")
        if options["out"]:
            Path(options["out"]).write_text(json.dumps(
                {"frozen": path.name, "causes": found.causes, "unexplained": found.unexplained},
                indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self.stdout.write(f"causes written to {options['out']}")
        if found.unexplained:
            for name, keys in list(found.unexplained.items())[:options["limit"]]:
                self.stdout.write(f"  unexplained: {name}: {', '.join(keys)}")
            raise CommandError(f"{len(found.unexplained)} card(s) moved for no listed cause")
        self.stdout.write("every card that moved has a listed cause")
