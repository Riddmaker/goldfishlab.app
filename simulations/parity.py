"""What players' corrections make of a card today, kept to compare (card model, phase 0).

A correction (`CardAnnotation.overrides`) is written against today's `Card`
fields. The rebuild moves cards onto blocks, and every stored correction has
to keep meaning what it meant: the card it makes must stay the same. This
module turns correction rows into **readings** - the card the engine plays
with that correction, in the form `simulations.coverage` snapshots - so the
same rows can be read again by later code and compared.

Three sources of rows:

* the built-in corrections (no owner, no deck) - seeded from the reference
  deck;
* the test decks' corrections (`decks.fixtures.SHAPES`);
  both committed as `tests/fixtures/parity_corpus.json`;
* real corrections from production, exported with `annotations_export`:
  card, scope and overrides only - no owner, no deck. That file stays on the
  operator's machine and is never committed.
"""

import json
from pathlib import Path

from django.conf import settings

from simulation import ENGINE_VERSION
from simulations import coverage

CORPUS = Path(settings.BASE_DIR) / "tests" / "fixtures" / "parity_corpus.json"


def rows(queryset=None) -> list[dict]:
    """Correction rows without who made them: card, scope and overrides.

    Identical rows are counted, not repeated: the same correction on the same
    card by two players is one row with ``count`` 2.
    """
    from simulations.models import CardAnnotation

    queryset = queryset if queryset is not None else CardAnnotation.objects.all()
    found: dict[tuple, dict] = {}
    for row in queryset.select_related("oracle_card").order_by("oracle_card__name", "pk"):
        if not row.overrides:
            continue
        key = (str(row.oracle_card_id), row.scope,
               json.dumps(row.overrides, sort_keys=True))
        entry = found.setdefault(key, {
            "oracle_id": str(row.oracle_card_id), "name": row.oracle_card.name,
            "scope": row.scope, "overrides": row.overrides, "count": 0,
            "engine_version": ENGINE_VERSION,
        })
        entry["count"] += 1
    return list(found.values())


def readings(entries: list[dict]) -> dict:
    """Each row's card as the engine plays it, by ``scope:name:n``.

    A player's row is read on top of the built-in one for the same card, as in
    a deck: the narrower scope wins key by key. No deck colours - a choice of
    colour is read the way `coverage` reads it.
    """
    from cards.models import OracleCard
    from simulations.engine import adapter

    builtin = {entry["oracle_id"]: entry["overrides"] for entry in entries
               if entry["scope"] == "builtin"}
    cards = {str(pk): card for pk, card in OracleCard.objects.select_related("profile")
             .in_bulk({entry["oracle_id"] for entry in entries}).items()}
    found, seen = {}, {}
    for entry in entries:
        card = cards.get(entry["oracle_id"])
        if card is None:
            continue
        merged = dict(builtin.get(entry["oracle_id"], {})) | entry["overrides"]
        made, gaps = adapter.card_with(card, merged)
        base = f"{entry['scope']}:{entry['name']}"
        seen[base] = seen.get(base, 0) + 1
        found[f"{base}:{seen[base]}"] = {"overrides": entry["overrides"],
                                         **coverage.reading(made, gaps)}
    return found


def write_corpus(found: dict, path: Path = CORPUS) -> None:
    coverage.write_snapshot(found, path)


def load_corpus(path: Path = CORPUS) -> dict:
    return coverage.load_snapshot(path)
