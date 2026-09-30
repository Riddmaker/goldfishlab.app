"""Regenerate the committed card/tag test fixtures from the Scryfall bulk files.

    py -3.13 scripts/build_card_fixtures.py <oracle_cards.jsonl.gz> <oracle_tags.jsonl.gz>

The fixtures are a deliberately small slice of the real bulk files: the 70 cards
of the reference Chainer deck, every card the test decks in `decks/fixtures.py`
are built from, plus a named list of edge cases that have each already broken
something once. They are committed so the test suite is deterministic and
offline - a suite that downloads 24 MB from a third party is not a suite, it is
a monitoring check for Scryfall.

Regenerate them when the reference deck or a test deck changes, not on a
schedule. A fixture that follows upstream around stops being a fixed point to
test against.

The bulk files are not kept in the repository. Fetch them the way the
application does, with `cards.scryfall.bulk_metadata` and `.download`, or point
this script at a copy you already have.
"""

import gzip
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from decks import fixtures as test_decks  # noqa: E402
from simulation.cards import COMMANDER, SPELLS, SWAMP, UTILITY_LANDS  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"

# Every one of these is in the fixture because it broke, or could break,
# something specific. The comment is the reason it may never be removed.
EDGE_CASES = {
    "Gleemax": "mana value 1,000,000 - overflowed a smallint column",
    "Tergrid, God of Fright": "double-faced: cost and text live on card_faces",
    "Cabal Coffers": "mana amount scales per Swamp; must stay unresolved",
    "Sol Ring": "produced_mana says ['C'], the real answer is 2",
    "Bojuka Bog": "enters tapped, unconditionally",
    "Charcoal Diamond": "enters tapped, and is an artifact",
    "Thriving Moor": "enters tapped with a rider",
    "Jet Medallion": "cost reducer a human calls a rock",
    "Ashnod's Altar": "tagged mana-rock, but a human calls it a sac engine",
    "Lim-Dul the Necromancer": "name needs accent folding to resolve",
    "Innocent Blood": "sacrifices, but is not a repeatable sac outlet",
    "Liliana's Caress": "drains via 'that player loses', not a drain tag",
    "Syr Konrad, the Grim": "deals damage rather than causing life loss",
    "Blood Crypt": "enters tapped only 'unless' - conditional",
    # The reference deck holds exactly three Game Changers, so without a fourth
    # the "this deck is Bracket 4" path has nothing to fire on.
    "Rhystic Study": "a fourth Game Changer, to exercise the bracket-4 verdict",
}

DECK_NAMES = {card.name for card in (COMMANDER, SWAMP, *UTILITY_LANDS, *SPELLS)}

#: Everything the shapes in `decks/fixtures.py` are built from. Read from that
#: module rather than restated here, so that adding a card to a test deck
#: cannot leave the sample one card short - a failure that reads exactly like
#: "the catalogue does not have this card" and sends you looking upstream.
TEST_DECK_NAMES = test_decks.card_names()

WANTED = DECK_NAMES | TEST_DECK_NAMES | set(EDGE_CASES)

# Layouts the ingester drops. Two are kept in the fixture precisely so the test
# can assert that they are dropped.
NON_CARD_LAYOUTS = {"art_series", "token"}
KEEP_NON_CARDS = 2

def front(name: str) -> str:
    return name.split("//")[0].strip()


def fold(name: str) -> str:
    """Accent-insensitive comparison, so 'Lim-Dul' finds 'Lim-Dul'."""
    import unicodedata

    text = unicodedata.normalize("NFKD", front(name))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def main(cards_path: str, tags_path: str) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    wanted_folded = {fold(n) for n in WANTED}

    chosen: dict[str, dict] = {}
    non_cards: list[dict] = []
    with gzip.open(cards_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            # A non-card is kept only as one of the two deliberate ones. It must
            # never reach the name match below: an art series shares its card's
            # name, so every wanted card was dragging its art series in behind
            # it and the sample was a third padding.
            if row.get("layout") in NON_CARD_LAYOUTS:
                if len(non_cards) < KEEP_NON_CARDS:
                    non_cards.append(row)
                continue
            if fold(row.get("name", "")) in wanted_folded:
                chosen.setdefault(row["oracle_id"], row)

    found = {fold(r["name"]) for r in chosen.values()}
    for name in sorted(WANTED):
        if fold(name) not in found:
            print(f"  ! not found in bulk data: {name}")

    rows = [*chosen.values(), *non_cards]
    _write(FIXTURE_DIR / "oracle_cards_sample.jsonl.gz", rows)
    print(f"cards fixture: {len(rows)} rows ({len(non_cards)} deliberate non-cards)")

    oracle_ids = set(chosen)
    _write(FIXTURE_DIR / "oracle_tags_sample.jsonl.gz", _select_tags(tags_path, oracle_ids))


def _select_tags(tags_path: str, oracle_ids: set[str]) -> list[dict]:
    """Every tag touching a sampled card, plus all of its ancestors.

    The ancestors are the point: without them the rollup has nothing to roll up
    to, and the test that proves the rollup works would pass vacuously.
    """
    tags = {}
    with gzip.open(tags_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            tag = json.loads(line)
            tags[tag["id"]] = tag

    direct = {
        tag_id
        for tag_id, tag in tags.items()
        if any(t["oracle_id"] in oracle_ids for t in tag.get("taggings") or [])
    }

    needed = set(direct)
    queue = deque(direct)
    while queue:
        tag = tags.get(queue.popleft())
        for parent in (tag or {}).get("parent_ids") or []:
            if parent in tags and parent not in needed:
                needed.add(parent)
                queue.append(parent)

    selected = []
    for tag_id in needed:
        tag = dict(tags[tag_id])
        # Keep only taggings pointing at cards the fixture actually contains,
        # so no foreign key can dangle and no count can be inflated.
        tag["taggings"] = [t for t in tag.get("taggings") or [] if t["oracle_id"] in oracle_ids]
        tag["child_ids"] = [c for c in tag.get("child_ids") or [] if c in needed]
        tag["parent_ids"] = [p for p in tag.get("parent_ids") or [] if p in needed]
        selected.append(tag)

    print(f"tags fixture: {len(selected)} tags ({len(direct)} direct, rest are ancestors)")
    return selected


def _write(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
