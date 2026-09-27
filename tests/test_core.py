"""Core pages, the health probe and the design-token reader."""

import pytest
from django.urls import reverse

from core.tokens import color_families

pytestmark = pytest.mark.django_db


def test_home_renders(client):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert b"it actually plays your deck" in response.content


def test_home_carries_the_fan_content_disclaimer(client):
    """Required by the WotC Fan Content Policy, in the wording it prescribes.

    See docs/phases/phase-8-launch.md.
    """
    response = client.get(reverse("home"))
    assert b"Goldfish Lab is unofficial Fan Content permitted under the" in response.content
    assert b"Not approved/endorsed by Wizards." in response.content
    assert "Wizards of the Coast. ©Wizards of the Coast LLC.".encode() in response.content


def test_every_page_credits_the_data_sources(client):
    """Commander Spellbook asks to be credited and linked; Scryfall is the card data."""
    response = client.get(reverse("home"))
    assert b'href="https://commanderspellbook.com"' in response.content
    assert b'href="https://scryfall.com"' in response.content


def test_styleguide_renders_every_family(client):
    response = client.get(reverse("styleguide"))
    assert response.status_code == 200
    for family in ("ink", "parchment", "blood", "verdigris"):
        assert family.encode() in response.content


def test_healthz_reports_ok(client):
    """The probe must be unauthenticated and must name what it checked."""
    response = client.get(reverse("healthz"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["database"] == "ok"


def test_healthz_needs_no_login(client):
    """Jelastic and uptime monitors cannot log in."""
    assert client.get(reverse("healthz")).status_code == 200


def test_token_reader_finds_all_families():
    families = color_families(use_cache=False)
    names = [f["name"] for f in families]
    assert names == ["ink", "parchment", "blood", "verdigris"]


def test_token_reader_returns_sorted_steps():
    ink = next(f for f in color_families(use_cache=False) if f["name"] == "ink")
    steps = [s["step"] for s in ink["steps"]]
    assert steps == sorted(steps)
    assert steps[0] == 50
    assert steps[-1] == 950


def test_token_values_are_hex():
    for family in color_families(use_cache=False):
        for step in family["steps"]:
            assert step["value"].startswith("#")
            assert len(step["value"]) == 7
