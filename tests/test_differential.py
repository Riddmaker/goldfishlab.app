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


# --- gate B: every card that moved has a cause --------------------------------


def _edit_frozen(path, name, change):
    import gzip
    import json

    data = differential.load(path)
    change(data["cards"][name]["read"])
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(data, handle)


def test_nothing_moved_needs_no_cause(frozen):
    path, _count = frozen
    found = differential.explain(path)
    assert found.causes == {}
    assert found.unexplained == {}


def test_a_change_no_cause_covers_is_unexplained(frozen):
    path, _count = frozen
    card = OracleCard.objects.get(name="Sol Ring")
    card.oracle_text = "{T}: Add {C}{C}{C}."
    card.save(update_fields=["oracle_text"])

    found = differential.explain(path)
    assert found.causes == {differential.key(card): []}
    assert "mana_abilities" in found.unexplained[differential.key(card)]
    assert "replay" not in found.unexplained[differential.key(card)]


def test_a_draw_in_reminder_text_is_the_readers_cause(frozen):
    path, _count = frozen
    card = OracleCard.objects.get(name="Llanowar Elves")
    card.oracle_text += "\nCycling {2} ({2}, Discard this card: Draw a card.)"
    card.save(update_fields=["oracle_text"])
    differential.freeze(path)
    # The frozen code read the reminder's draw as one more card on cast.
    _edit_frozen(path, differential.key(card), lambda read: read.update(draw_on_cast=1))

    found = differential.explain(path)
    assert found.causes == {differential.key(card): ["reader:draw in reminder text"]}
    assert found.unexplained == {}


def test_a_forge_family_that_changed_the_profile_is_the_cause(frozen, monkeypatch):
    from cards import forge_import, forge_merge

    forge_import.run()
    path, _count = frozen
    differential.freeze(path)

    def tapped(profile, abilities, card):
        if card.name == "Sol Ring":
            profile.enters_tapped = True

    monkeypatch.setattr(forge_merge, "FAMILIES", [*forge_merge.FAMILIES, tapped])
    found = differential.explain(path)
    sol_ring = differential.key(OracleCard.objects.get(name="Sol Ring"))
    assert found.causes == {sol_ring: ["forge:tapped"]}
    assert found.unexplained == {}
