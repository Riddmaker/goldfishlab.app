"""The whole catalogue, read by frozen code and by today's code (card model, phase 0).

The coverage gate (`simulations.coverage`) watches a fixed set of 3,070 cards.
The card-model rebuild replaces the reader and the engine's card under every
Commander-legal card, so before it starts, today's reading of all of them is
**frozen** into a local file, and every later step is compared against it:

    manage.py engine_coverage --freeze   # once, on engine v27
    manage.py engine_coverage --diff     # after a change: every card that moved

Both sides derive the profiles in memory from the same catalogue, so a change
to the reader shows up even before `profiles.rebuild()` ran, and nothing is
written to the database. The file lives in `/.data/` (not committed: it is
about 2 MB, and only means something next to the catalogue it was made from).
It records which Scryfall imports that catalogue came from; when they differ
at `--diff`, new oracle text or tags explain part of the diff, and it says so.
"""

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings

from simulation import ENGINE_VERSION
from simulations import coverage
from simulations.engine import adapter

FROZEN_DIR = Path(settings.BASE_DIR) / ".data"

#: Cards derived and read per round trip to the database.
CHUNK = 1_000


def frozen_path(version: int = ENGINE_VERSION) -> Path:
    return FROZEN_DIR / f"frozen_engine_v{version}.json.gz"


def latest_frozen() -> Path | None:
    """The frozen file of the highest engine version on disk."""
    found = sorted(FROZEN_DIR.glob("frozen_engine_v*.json.gz"),
                   key=lambda path: int(path.name.split("_v")[1].split(".")[0]))
    return found[-1] if found else None


def catalogue():
    """Every Commander-legal card: what players can bring."""
    from cards.models import OracleCard

    return OracleCard.objects.filter(legalities__commander="legal").order_by("pk")


def key(card) -> str:
    """Name and the start of the oracle id: names are not unique in the catalogue."""
    return f"{card.name} [{str(card.pk)[:8]}]"


def readings(oracle_cards) -> dict:
    """Each card as the reader and the engine alone read it today, by `key`.

    The profile is derived here rather than loaded, so the reading is the
    code's and not whatever the last `profiles.rebuild()` stored.
    """
    from cards import profiles
    from cards.models import OracleCardTag

    branches = profiles.tutor_branches()
    revivals = profiles.reanimate_branches()
    found = {}
    cards = list(oracle_cards)
    for start in range(0, len(cards), CHUNK):
        chunk = cards[start:start + CHUNK]
        slugs: dict[str, set[str]] = {}
        links = OracleCardTag.objects.filter(oracle_card__in=chunk).values_list(
            "oracle_card_id", "tag__slug")
        for card_id, slug in links:
            slugs.setdefault(str(card_id), set()).add(slug)
        for card in chunk:
            # Sets the reverse one-to-one cache only; nothing is saved.
            card.profile = profiles.derive(card, slugs.get(str(card.pk), set()),
                                           branches=branches, revivals=revivals)
        read = adapter.engine_readings(chunk, builtin=False)
        for card in chunk:
            found[key(card)] = {"id": str(card.pk), **coverage.reading(*read[card.pk])}
    return found


def imports() -> dict:
    """The Scryfall imports the catalogue in this database came from, by kind."""
    from cards.models import BulkImport

    latest = {}
    for row in BulkImport.objects.filter(status=BulkImport.Status.OK).order_by("started_at"):
        latest[row.kind] = row.scryfall_updated_at.isoformat()
    return latest


def freeze(path: Path | None = None) -> int:
    """Write today's reading of the whole catalogue; return how many cards."""
    path = path or frozen_path()
    found = readings(catalogue())
    data = {
        "engine_version": ENGINE_VERSION,
        "frozen_at": datetime.now(UTC).isoformat(),
        "imports": imports(),
        "cards": found,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"), ensure_ascii=False)
    return len(found)


def load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def diff(path: Path) -> tuple[coverage.Diff, dict, dict, list[str]]:
    """Today's reading against the frozen one: the diff, both sides, and notes."""
    frozen = load(path)
    now = readings(catalogue())
    notes = []
    if frozen["imports"] != imports():
        notes.append("the catalogue was imported again since the freeze: new oracle text "
                     "or tags explain part of this diff "
                     f"(frozen {frozen['imports']}, now {imports()})")
    return coverage.compare(frozen["cards"], now), frozen["cards"], now, notes
