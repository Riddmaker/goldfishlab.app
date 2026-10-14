"""Check a new pin against the old one, without a database.

    manage.py forge_check --old OLD.jsonl.gz [--new NEW.jsonl.gz] [--out body.md]

Translates every script of both packed copies (`cards.forge_import.compare`)
and writes the pin pull request's description to ``--out``. Prints one line,
``changes=N`` (scripts added, removed or changed), to standard output, so the
weekly sync (``.github/workflows/forge-sync.yml``) can append it to
``$GITHUB_OUTPUT``; everything else goes to standard error.

Fails when more than `forge_import.MAX_FAILURES` of the new scripts fail to
translate: the sync then opens no pull request, and the pin stays.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from cards import forge, forge_import


class Command(BaseCommand):
    help = "Compare a new packed copy of Forge's scripts with the old one."

    # Runs in the sync workflow, where no database is set up.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--old", required=True, help="the pinned pack before forge_pack")
        parser.add_argument("--new", default=str(forge.PACK))
        parser.add_argument("--out", help="where to write the pull request description")

    def handle(self, *args, **options):
        comparison = forge_import.compare(Path(options["old"]), Path(options["new"]))
        if options["out"]:
            Path(options["out"]).write_text(forge_import.report(comparison), encoding="utf-8")
        self.stderr.write(
            f"{comparison.old_commit[:10]} -> {comparison.new_commit[:10]}: "
            f"{len(comparison.added)} added, {len(comparison.removed)} removed, "
            f"{len(comparison.changed)} changed, {len(comparison.blocks_changed)} with other "
            f"blocks, {len(comparison.failed)} of {comparison.scripts} failed")
        if comparison.too_many_failures:
            raise CommandError(
                f"{len(comparison.failed)} of {comparison.scripts} scripts failed to translate, "
                f"first: {comparison.failed[:3]}")
        self.stdout.write(f"changes={comparison.changes}")
