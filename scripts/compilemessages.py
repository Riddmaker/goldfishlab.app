"""Compile every translation catalogue (locale/*/LC_MESSAGES/*.po) to .mo.

Usage:  ./.venv/Scripts/python.exe scripts/compilemessages.py

Django's `compilemessages` calls GNU `msgfmt`, which exists neither on Windows
nor in the production image. polib (pure Python, a dev dependency) writes the
same file: only translated entries, never fuzzy or obsolete ones - exactly what
`msgfmt` does by default. Both the .po and the .mo are committed (as Django and
allauth ship theirs), so the image never compiles anything and stays as it is;
`tests/test_i18n.py` fails when a .mo no longer matches its .po.

Extracting the strings (`makemessages`) needs GNU `xgettext` as well; that runs
in a throwaway container:  docker compose run --rm i18n
"""

import sys
from pathlib import Path

import polib

LOCALE = Path(__file__).resolve().parent.parent / "locale"


def compile_all() -> list[Path]:
    written = []
    for po_path in sorted(LOCALE.glob("*/LC_MESSAGES/*.po")):
        mo_path = po_path.with_suffix(".mo")
        polib.pofile(str(po_path)).save_as_mofile(str(mo_path))
        written.append(mo_path)
    return written


if __name__ == "__main__":
    for path in compile_all():
        print(path.relative_to(LOCALE.parent))
    sys.exit(0)
