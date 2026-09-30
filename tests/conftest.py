"""Shared pytest configuration.

Note on `slow`: the marker is applied here rather than edited into
tests/test_statistics.py so that the vendored engine tests stay byte-identical
to their originals in the magic-project repository. That equality is the whole
safety net for the Phase 2 generalization - the moment those files start
drifting, "all 65 tests still green" stops meaning anything.
"""

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
