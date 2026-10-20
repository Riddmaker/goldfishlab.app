"""PR 18: the suite runs in parallel, one Redis cache database per worker.

Every test empties the cache, and RedisCache.clear() is a FLUSHDB of the whole
database. Two workers on one database would wipe each other's counters.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from goldfishlab.settings.test import worker_cache

SERIAL = "redis://localhost:6379/1"


def test_the_serial_run_keeps_its_database():
    assert worker_cache(SERIAL, "") == SERIAL


def test_each_worker_has_a_database_of_its_own():
    assert worker_cache(SERIAL, "gw0") == "redis://localhost:6379/2"
    assert worker_cache(SERIAL, "gw13") == "redis://localhost:6379/15"


def test_a_worker_past_the_sixteen_databases_is_refused():
    with pytest.raises(ImproperlyConfigured, match="-n 14 or fewer"):
        worker_cache(SERIAL, "gw14")
