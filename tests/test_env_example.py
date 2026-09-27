"""`.env.example` must name every environment variable the settings read.

CLAUDE.md HABIT 5 says example configs reflect the actual required keys without
containing real secrets. That is a rule about a file nobody opens until they
are setting the project up for the first time - which is the worst possible
moment to discover that a key was added three phases ago and never written
down. The symptom is not an error message, either: `env("X", default="")`
returns the default, the feature is simply off, and the person has no way to
know it exists.

So the rule is checked rather than remembered. The check is deliberately
one-directional: every variable the code reads must be documented, but
`.env.example` may name more than the code reads - `MISTRAL_API_KEY` is
commented out there precisely because nothing reads it yet, and that comment is
the documentation.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "goldfishlab" / "settings"
ENV_EXAMPLE = ROOT / ".env.example"

#: `env("NAME"`, `env.bool("NAME"`, `env.int(`, `env.list(`, `env.db(` ...
_ENV_READ = re.compile(r'env(?:\.\w+)?\(\s*"([A-Z][A-Z_0-9]*)"')


def _variables_read() -> set[str]:
    names: set[str] = set()
    for module in SETTINGS.glob("*.py"):
        names.update(_ENV_READ.findall(module.read_text(encoding="utf-8")))
    return names


def test_the_settings_read_something():
    """Guard against the regex silently matching nothing and passing forever."""
    assert len(_variables_read()) > 5


@pytest.mark.parametrize("name", sorted(_variables_read()))
def test_every_variable_the_settings_read_is_in_env_example(name):
    """Parametrised so a failure names the missing key rather than a count."""
    documented = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert name in documented, (
        f"goldfishlab/settings reads {name}, but .env.example never mentions "
        f"it. Add it - commented out with the reason if nothing should set it "
        f"locally. See CLAUDE.md HABIT 5."
    )


def test_env_example_holds_no_value_for_a_secret():
    """It is committed, so a filled-in secret here is a published secret.

    Checked on the keys whose names say they are credentials rather than on
    every line, because `SCRYFALL_USER_AGENT` has a perfectly good default
    value that belongs in the file.
    """
    secrets = ("SECRET_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "SENTRY_DSN")
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() in secrets:
            assert value.strip() == "", (
                f"{key.strip()} has a value in .env.example, which is committed. "
                f"Leave it empty."
            )
