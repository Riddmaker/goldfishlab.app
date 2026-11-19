"""Check the weekly news files (PR 22): `python -m news.check`.

Plain Python, so the routine that writes the news can run it before it opens
its pull request, without the site's dependencies:

    python -m news.check                         # every week, the rules only
    python -m news.check --week 2026-W46 --sources /tmp/news-articles

With `--sources` the week is also checked against the articles it was written
from (`news.rules.against`). Exit code 0 when everything holds, 1 otherwise,
with one line per problem.
"""

import argparse
import sys
from pathlib import Path

from news import rules

FOLDER = Path(__file__).resolve().parent / "issues"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m news.check")
    parser.add_argument("--folder", type=Path, default=FOLDER)
    parser.add_argument("--week", help="the week to check against its articles")
    parser.add_argument("--sources", type=Path,
                        help="a folder with <slug>-<n>.txt, the text of each link")
    args = parser.parse_args(argv)
    if bool(args.week) != bool(args.sources):
        parser.error("--week and --sources go together")
    try:
        issues = rules.load(args.folder)
    except rules.Invalid as error:
        print(f"FAIL {error}")
        return 1
    problems = []
    if args.week:
        issue = next((issue for issue in issues if issue["week"] == args.week), None)
        if issue is None:
            print(f"FAIL {args.week}: no such file in {args.folder}")
            return 1
        problems = rules.against(issue, args.sources)
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print(f"OK {len(issues)} weeks")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
