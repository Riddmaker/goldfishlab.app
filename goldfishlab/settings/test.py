"""Test settings: development's, with a password hasher built for speed.

Django's own advice ("Speeding up the tests"): the default PBKDF2 hasher is
slow on purpose, and the suite pays it on every `create_user` and every login -
0.7 s each, measured 2026-09-30, which made the two rate-limit loop tests in
`test_security.py` take 14 s apiece. MD5 is exactly as good at proving that a
test user's password matches, and it is never used outside the test suite:
pytest selects this module (pyproject.toml, and the CI job's environment).
"""

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
