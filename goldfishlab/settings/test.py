"""Test settings: development's, with a password hasher built for speed.

Django's own advice ("Speeding up the tests"): the default PBKDF2 hasher is
slow on purpose, and the suite pays it on every `create_user` and every login -
0.7 s each, measured 2026-09-30, which made the two rate-limit loop tests in
`test_security.py` take 14 s apiece. MD5 is exactly as good at proving that a
test user's password matches, and it is never used outside the test suite:
pytest selects this module (pyproject.toml, and the CI job's environment).
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .dev import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Never the developer's real Mistral key from .env: a test that forgot to stub
# the client must fail on "not configured", not spend money and send a deck
# list to a third party. Tests that need it set their own fake value.
MISTRAL_API_KEY = ""

# English alone, whatever .env switches on for local browsing: a test that
# needs another language sets `LANGUAGES` itself (tests/test_i18n.py).
LANGUAGES_ON = []
LANGUAGES = LANGUAGES[:1]  # noqa: F405

# Never the production redirect, whatever .env says: the tests that set
# SITE_URL ask on "testserver" and expect the page, not a 301
# (tests/test_canonical_host.py switches it on itself).
SITE_URL_REDIRECT = False

# A cache database of its own per pytest-xdist worker (gw0, gw1, ...). The
# suite empties the cache around every test (tests/conftest.py), and Django's
# RedisCache.clear() is a FLUSHDB: with one database for all, a worker would
# wipe the rate-limit counters another worker is counting on. Database 0 is the
# Celery broker and 1 the serial run's, so worker N takes 2 + N; Redis has 16,
# which caps a parallel run at 14 workers.
def worker_cache(location, worker):
    """The cache LOCATION for an xdist worker id; the serial run keeps its own."""
    if not worker:
        return location
    database = 2 + int(worker.removeprefix("gw"))
    if database > 15:
        raise ImproperlyConfigured(
            f"pytest worker {worker} has no Redis database left: run with -n 14 or fewer"
        )
    return location.rsplit("/", 1)[0] + f"/{database}"


CACHES["default"]["LOCATION"] = worker_cache(  # noqa: F405
    CACHES["default"]["LOCATION"], os.environ.get("PYTEST_XDIST_WORKER", "")  # noqa: F405
)
