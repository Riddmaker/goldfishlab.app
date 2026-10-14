"""Card model phase 0: what today's corrections make of a card, kept to compare.

The built-in corrections (the reference deck's) and the test decks' are read
on the offline sample catalogue, and each card they make must equal the
committed corpus `tests/fixtures/parity_corpus.json`. Later phases read the
same rows with the block model and compare. When a change of today's engine
moves a reading on purpose, look at the diff and rewrite the corpus with

    PARITY_WRITE=1 pytest tests/test_parity.py
"""

import io
import json
import os

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from decks import seeding
from simulations import coverage, parity
from simulations.models import CardAnnotation
from tests.support import forget_module_rows, load_catalogue, require_test_database

pytestmark = pytest.mark.django_db

WRITE = os.environ.get("PARITY_WRITE") == "1"


@pytest.fixture(scope="module")
def corrected(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        require_test_database()
        forget_module_rows()
        load_catalogue()
        call_command("seed_reference_deck", verbosity=0)
        owner = get_user_model().objects.create_user(email="parity@example.com",
                                                     password="pw-for-test-only")
        seeding.build_all(owner)
        yield
        forget_module_rows()


@pytest.fixture(scope="module")
def found(corrected, django_db_blocker):
    with django_db_blocker.unblock():
        readings = parity.readings(parity.rows())
    if WRITE:
        parity.write_corpus(readings)
    return readings


def test_every_correction_makes_the_committed_card(found):
    committed = parity.load_corpus()
    diff = coverage.compare(committed, found)
    assert diff.empty, "\n" + "\n".join(diff.lines(committed, found))


def test_the_corpus_holds_built_in_and_deck_corrections(found):
    scopes = {key.split(":", 1)[0] for key in found}
    assert scopes == {"builtin", "deck"}
    assert sum(key.startswith("builtin:") for key in found) >= 60


def test_the_export_names_no_owner_and_no_deck(corrected, django_db_blocker, tmp_path):
    out = tmp_path / "corrections.json"
    with django_db_blocker.unblock():
        call_command("annotations_export", out=str(out), stdout=io.StringIO())
        stored = CardAnnotation.objects.exclude(overrides={}).count()
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert {key for row in rows for key in row} == {
        "oracle_id", "name", "scope", "overrides", "patches", "count", "engine_version"}
    assert sum(row["count"] for row in rows) == stored


def test_the_export_does_not_overwrite(corrected, django_db_blocker, tmp_path):
    from django.core.management.base import CommandError

    out = tmp_path / "corrections.json"
    out.write_text("[]", encoding="utf-8")
    with django_db_blocker.unblock(), pytest.raises(CommandError):
        call_command("annotations_export", out=str(out))


def test_every_correction_plays_the_same_from_its_patches(corrected, django_db_blocker):
    """Card model gate C: a correction as patches says exactly what it said."""
    from simulations import patches

    with django_db_blocker.unblock():
        rows = list(CardAnnotation.objects.exclude(overrides={})
                    .select_related("oracle_card__profile"))
    assert rows
    for row in rows:
        profile = getattr(row.oracle_card, "profile", None)
        said, notes = patches.effective(row.overrides, row.patches, profile)
        assert (said, notes) == (row.overrides, []), row.oracle_card.name
