"""Helpers the test modules share and that are not fixtures.

Kept out of `conftest.py` because pytest's advice is never to import from a
conftest: it is loaded by pytest's own machinery, and a module that imports it
as well can end up with two copies.
"""

from datetime import UTC, datetime
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: The fixtures' stand-in for a bulk file's timestamp. Fixed, not `now()`, so
#: every run ingests "the same version" and the idempotency check behaves.
CATALOGUE_VERSION = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)


def require_test_database() -> None:
    """Refuse to write or delete anywhere but pytest's own database.

    pytest-django creates the test database only when a collected test asks
    for one. A module whose tests only use a module-scoped fixture, run on its
    own, therefore stayed on the development database - and its cleanup
    emptied it (P19, 2026-10-08). Such a module needs
    `pytestmark = pytest.mark.django_db`; this is the net under that.
    """
    from django.db import connection

    name = connection.settings_dict["NAME"]
    if not str(name).startswith("test_"):
        raise RuntimeError(f"refusing to touch {name!r}: not a test database")


def load_catalogue() -> None:
    """The offline sample: its cards, their tags, and a profile for each.

    About a quarter of a second (measured 2026-09-30), so it stays per test
    rather than loaded once for the session: the ingest tests need the empty
    database a fresh test starts with.
    """
    from cards import ingest, profiles

    require_test_database()
    ingest.ingest_cards(source=FIXTURES / "oracle_cards_sample.jsonl.gz",
                        updated_at=CATALOGUE_VERSION)
    ingest.ingest_tags(source=FIXTURES / "oracle_tags_sample.jsonl.gz",
                       updated_at=CATALOGUE_VERSION)
    profiles.rebuild()


def forget_module_rows() -> None:
    """Undo what a module-scoped fixture wrote outside any test's transaction.

    Such a fixture (`django_db_blocker.unblock()`) saves the catalogue and a
    seeded deck for real, and without this every later module found them -
    one user, one deck, 70 annotations and 96 cards (found in phase 9 G and
    measured in I). Deleting the users takes their decks, runs and
    subscriptions with them; the cards take their tags and profiles. What the
    migrations made (plans, permissions) stays.
    """
    from django.contrib.auth import get_user_model

    from cards.models import BulkImport, OracleCard, Tag
    from simulations.models import CardAnnotation

    require_test_database()
    CardAnnotation.objects.all().delete()
    get_user_model().objects.all().delete()
    OracleCard.objects.all().delete()
    Tag.objects.all().delete()
    BulkImport.objects.all().delete()
