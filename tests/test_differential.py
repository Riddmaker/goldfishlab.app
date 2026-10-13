"""Card model phase 0: the whole catalogue frozen, and today's code against it.

`simulations.differential` freezes how the reader and the engine read every
Commander-legal card, and later lists every card that reads otherwise. These
tests run it on the offline sample catalogue.
"""

from datetime import timedelta

import pytest

from cards.models import BulkImport, OracleCard
from simulations import differential


@pytest.fixture
def frozen(catalogue, tmp_path):
    path = tmp_path / "frozen_engine_v1.json.gz"
    count = differential.freeze(path)
    return path, count


def test_freeze_covers_every_commander_legal_card(frozen):
    _path, count = frozen
    assert count == OracleCard.objects.filter(legalities__commander="legal").count() > 0


def test_unchanged_code_reads_every_card_as_frozen(frozen):
    path, _count = frozen
    diff, _before, _after, notes = differential.diff(path)
    assert diff.empty
    assert notes == []


def test_a_card_that_reads_otherwise_is_named(frozen):
    path, _count = frozen
    card = OracleCard.objects.get(name="Sol Ring")
    card.oracle_text = "{T}: Add {C}{C}{C}."
    card.save(update_fields=["oracle_text"])

    diff, _before, _after, _notes = differential.diff(path)
    assert [differential.key(card)] == diff.changed + diff.lost


def test_a_new_import_is_named_as_a_cause(frozen):
    path, _count = frozen
    latest = BulkImport.objects.filter(status=BulkImport.Status.OK).latest("started_at")
    BulkImport.objects.create(kind=latest.kind, status=BulkImport.Status.OK,
                              scryfall_updated_at=latest.scryfall_updated_at
                              + timedelta(days=1))

    _diff, _before, _after, notes = differential.diff(path)
    assert len(notes) == 1
    assert "imported again" in notes[0]


def test_the_newest_frozen_version_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(differential, "FROZEN_DIR", tmp_path)
    assert differential.latest_frozen() is None
    for version in (9, 27, 10):
        (tmp_path / f"frozen_engine_v{version}.json.gz").touch()
    assert differential.latest_frozen().name == "frozen_engine_v27.json.gz"


def test_a_new_forge_commit_is_named_as_a_cause(frozen):
    from cards import forge_import

    forge_import.run()
    path, _count = frozen
    differential.freeze(path)
    BulkImport.objects.filter(kind=BulkImport.Kind.FORGE_SCRIPTS).update(forge_commit="e" * 40)

    _diff, _before, _after, notes = differential.diff(path)
    assert notes == ["Forge's scripts are of another commit since the freeze "
                     "(ffffffffff, now eeeeeeeeee)"]
