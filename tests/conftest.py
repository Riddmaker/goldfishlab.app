"""Shared pytest configuration.

Note on `slow`: the marker is applied here rather than edited into
tests/test_statistics.py so that the vendored engine tests stay byte-identical
to their originals in the magic-project repository. That equality is the whole
safety net for the Phase 2 generalization - the moment those files start
drifting, "all 65 tests still green" stops meaning anything.
"""

import pytest
from django.core.cache import cache
from django.utils import translation

from tests.support import load_catalogue

# Engine test modules vendored from magic-project. They import no Django and
# must never need a database.
ENGINE_MODULES = {"test_cards", "test_mana", "test_game", "test_statistics"}

# Draws 200_000 samples in setUpClass to validate the Monte Carlo against the
# exact hypergeometric distribution. The most important test in the repo, and
# far too slow for the inner loop. test_karsten (P16) plays Karsten's table,
# about half a minute.
SLOW_MODULES = {"test_statistics", "test_karsten"}


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


@pytest.fixture(autouse=True)
def _english_between_tests():
    """Start every test in the default language. A request leaves its language
    active in the thread, and since P10 `reverse()` of a public page depends on
    it: /pricing/ after an English request, /de/pricing/ after a German one."""
    translation.deactivate()
    yield
    translation.deactivate()


# --- Forge's scripts: a small made-up pack instead of the pinned 34,000 -----

#: Made up in the format's shape for the tests; none is copied from Forge's
#: card data. Named after cards of the offline sample catalogue.
FORGE_SCRIPTS = {
    "s/sol_ring.txt": "Name:Sol Ring\nManaCost:1\nTypes:Artifact\n"
                      "A:AB$ Mana | Cost$ T | Produced$ C | Amount$ 2\n",
    "l/llanowar_elves.txt": "Name:Llanowar Elves\nManaCost:G\nTypes:Creature Elf Druid\n"
                            "PT:1/1\nA:AB$ Mana | Cost$ T | Produced$ G\n",
    "z/zz_not_a_card.txt": "Name:Not In The Catalogue\nManaCost:1\nTypes:Artifact\n",
}

#: The pack's commit: a made-up hash.
FORGE_COMMIT = "f" * 40


@pytest.fixture(scope="session")
def _forge_pack(tmp_path_factory):
    from cards import forge

    path = tmp_path_factory.mktemp("forge") / "forge_scripts.jsonl.gz"
    forge.pack(FORGE_SCRIPTS.items(), FORGE_COMMIT, "2026-10-13T08:00:00+00:00", path)
    return path


@pytest.fixture(autouse=True)
def _small_forge_pack(_forge_pack, monkeypatch):
    """Every catalogue refresh reads Forge's scripts; the pinned pack takes
    half a minute, so tests read three made-up scripts instead."""
    from cards import forge

    monkeypatch.setattr(forge, "PACK", _forge_pack)


# --- the card catalogue (the loader lives in tests/support.py) ---------------


@pytest.fixture
def catalogue(db):
    load_catalogue()


# --- rate limits ------------------------------------------------------------


@pytest.fixture
def pinned_window(monkeypatch):
    """Keep every django-ratelimit count inside one fixed window.

    It counts in fixed windows, so a loop of posts that straddles a window
    edge restarts the count and a limit test fails for no reason - seen with
    the admin login (twelve password hashes) and the simulation start (25
    posts). Pinning the window makes these tests measure the limit, not the
    clock. allauth's own limits count differently and are not touched.
    """
    import django_ratelimit.core

    monkeypatch.setattr(django_ratelimit.core, "_get_window",
                        lambda value, period: 4_102_444_800)
