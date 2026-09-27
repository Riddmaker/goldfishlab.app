"""Shared pytest configuration.

Note on `slow`: the marker is applied here rather than edited into
tests/test_statistics.py so that the vendored engine tests stay byte-identical
to their originals in the magic-project repository. That equality is the whole
safety net for the Phase 2 generalization - the moment those files start
drifting, "all 65 tests still green" stops meaning anything.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.core.cache import cache

# Engine test modules vendored from magic-project. They import no Django and
# must never need a database.
ENGINE_MODULES = {"test_cards", "test_mana", "test_game", "test_statistics"}

# Draws 200_000 samples in setUpClass to validate the Monte Carlo against the
# exact hypergeometric distribution. The most important test in the repo, and
# far too slow for the inner loop.
SLOW_MODULES = {"test_statistics"}


def pytest_collection_modifyitems(items):
    """Tag the statistical validation as slow without touching its source."""
    for item in items:
        if item.module.__name__.rsplit(".", 1)[-1] in SLOW_MODULES:
            item.add_marker(pytest.mark.slow)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Keep allauth's cache-based rate limits from leaking between tests."""
    cache.clear()
    yield
    cache.clear()


# --- printings ---------------------------------------------------------------
#
# Deliberately **not** part of the per-module `catalogue` fixtures, and this is
# a design decision rather than an omission.
#
# `default_cards` is an optional 78.8 MB download that a real installation may
# never have made, so "no printings at all" is a supported state that the whole
# application has to keep working in. Leaving it as the default for the suite
# means several hundred existing tests go on exercising that state for free,
# every run, instead of it being something nobody looks at until somebody
# deploys without it.
#
# A test that wants printings asks for `printings` *after* `catalogue`, because
# a printing carries a foreign key to its card.

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PRINTINGS_FIXTURE = FIXTURES / "default_cards_sample.jsonl.gz"

#: The fixtures' stand-in for a bulk file's timestamp. Fixed, not `now()`, so a
#: test asserting "prices as of" gets the same answer on every run.
PRINTINGS_VERSION = datetime(2026, 9, 20, 9, 5, tzinfo=UTC)


@pytest.fixture
def printings(db):
    """The 361 printings of the sampled cards, over 96 of the 98 cards.

    All of a card's printings, capped at four, never one: a fixture with a
    single printing per card would let a resolver that quietly ignores the set
    code pass every test here.
    """
    from cards import ingest

    return ingest.ingest_printings(
        source=PRINTINGS_FIXTURE, updated_at=PRINTINGS_VERSION
    )
