"""Regenerate only the printings fixture, from the cards fixture already committed.

    py -3.13 scripts/build_printing_fixture.py <default_cards.jsonl.gz>

Why this exists separately from `build_card_fixtures.py`, which can also write
it: **the three fixtures do not age at the same rate.** The card and tag
samples are a fixed point the whole suite is asserted against and should change
only when a deck changes. Printings carry prices, which move daily, and
`default_cards` is the only bulk file big enough to be annoying to fetch.

So this script takes the oracle ids out of the committed
`oracle_cards_sample.jsonl.gz` and writes `default_cards_sample.jsonl.gz` for
exactly those cards. The other two fixtures are read and never written, which
is the property that makes it safe to run on a whim.

Regenerating the whole set at once, when a deck has actually changed, is still
`build_card_fixtures.py` with three arguments.
"""

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_card_fixtures import (  # noqa: E402
    FIXTURE_DIR,
    _select_printings,
    _write,
)

CARDS_FIXTURE = FIXTURE_DIR / "oracle_cards_sample.jsonl.gz"


def oracle_ids_in_fixture() -> set[str]:
    """Every oracle id the committed card sample contains.

    Read from the fixture rather than recomputed from `decks/fixtures.py`,
    because the question here is "which cards does the test database actually
    have", and a printing for a card the suite has never heard of is a foreign
    key with nothing behind it.
    """
    found = set()
    with gzip.open(CARDS_FIXTURE, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("oracle_id"):
                found.add(row["oracle_id"])
    return found


def main(printings_path: str) -> None:
    oracle_ids = oracle_ids_in_fixture()
    print(f"cards fixture holds {len(oracle_ids)} oracle ids")
    rows = _select_printings(printings_path, oracle_ids)
    _write(FIXTURE_DIR / "default_cards_sample.jsonl.gz", rows)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
