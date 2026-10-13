"""Move the pin: pack Forge's card scripts of one commit into the repository.

    manage.py forge_pack                       # HEAD of the checkout in .data/forge
    manage.py forge_pack --forge DIR --commit 0123abc...

Reads the scripts from a git checkout of Forge (a sparse clone of
``forge-gui/res/cardsfolder`` will do) and writes ``cards/data/
forge_scripts.jsonl.gz`` and ``cards/data/forge_commit.txt``. Commit both in
one pull request: the pin moves only by review, so a deck's numbers never
change between two deploys of the same code.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cards import forge


class Command(BaseCommand):
    help = "Pack Forge's card scripts of one commit as the pinned copy."

    def add_arguments(self, parser):
        parser.add_argument("--forge", default=str(Path(settings.BASE_DIR) / ".data" / "forge"))
        parser.add_argument("--commit", default="HEAD")

    def handle(self, *args, **options):
        import subprocess  # noqa: S404 - git, no shell

        root = Path(options["forge"])
        try:
            commit = subprocess.run(  # noqa: S603
                ["git", "-C", str(root), "rev-parse", "--verify",  # noqa: S607
                 f"{options['commit']}^{{commit}}"],
                capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise CommandError(f"no commit {options['commit']!r} in {root}") from error
        date = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "log", "-1", "--format=%cI", commit],  # noqa: S607
            capture_output=True, text=True, check=True).stdout.strip()
        count = forge.pack(forge.from_checkout(root, commit), commit, date)
        forge.PIN.write_text(commit + "\n", encoding="utf-8")
        self.stdout.write(f"packed {count} scripts of {commit[:10]} into {forge.PACK.name}")
