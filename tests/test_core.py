"""Core pages, the health probe and the design-token reader."""

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

from core.tokens import color_families

pytestmark = pytest.mark.django_db


def test_home_renders(client):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert b"it actually plays your deck" in response.content
    assert b"Know your deck before game night." in response.content


def test_step_one_is_not_only_for_archidekt(client):
    """Phase 10 T1.3: any deck site's export, or a list copied by hand."""
    body = client.get(reverse("home")).content.decode()

    assert "Export or copy your list" in body
    assert "Archidekt, Moxfield, ManaBox" in body
    assert "Export from Archidekt" not in body


def test_home_carries_the_fan_content_disclaimer(client):
    """Required by the WotC Fan Content Policy, in the wording it prescribes.

    (Phase 8.)
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


def test_styleguide_renders_every_family(client, settings):
    settings.DEBUG = True
    response = client.get(reverse("styleguide"))
    assert response.status_code == 200
    for family in ("ink", "parchment", "blood", "verdigris"):
        assert family.encode() in response.content


def test_styleguide_is_a_development_tool(client, settings):
    """Production neither serves the styleguide nor links it from the footer."""
    settings.DEBUG = False
    assert client.get(reverse("styleguide")).status_code == 404
    assert b"/styleguide/" not in client.get(reverse("home")).content

    settings.DEBUG = True
    assert b"/styleguide/" in client.get(reverse("home")).content


def test_every_page_has_a_tab_icon(client):
    """A tab with no icon looked shady on the first live visit (phase 9 A)."""
    body = client.get(reverse("home")).content.decode()
    assert '<link rel="icon"' in body
    assert "img/favicon" in body
    assert finders.find("img/favicon.svg"), "the icon the pages link to exists"


def test_the_old_favicon_address_points_at_the_icon(client):
    """For whatever asks /favicon.ico directly, such as the admin."""
    response = client.get("/favicon.ico")
    assert response.status_code == 302
    assert "img/favicon" in response["Location"]


def test_the_header_is_about_decks(client, django_user_model):
    """Decks and Import in the header; the collection is not the product."""
    user = django_user_model.objects.create_user(email="h@example.com", password="pw-x-1234")
    client.force_login(user)
    body = client.get(reverse("home")).content.decode()

    assert f'href="{reverse("decks:list")}"' in body
    assert f'href="{reverse("decks:import")}"' in body
    assert ">Collection<" not in body


def test_the_collection_is_gone(client, django_user_model):
    """Phase 9 B: no page, and no legal page still promising to keep one."""
    user = django_user_model.objects.create_user(email="c@example.com", password="pw-x-1234")
    client.force_login(user)

    assert client.get("/collection/").status_code == 404
    for name in ("privacy", "terms", "accounts:data"):
        assert "collection" not in client.get(reverse(name)).content.decode().lower()


def test_the_collection_app_and_the_printings_leave_nothing_behind():
    """Phase 9 I: the app is uninstalled, and its content types are cleaned up.

    Django deletes no content type by itself, so `cards/0009` does what
    `remove_stale_contenttypes --include-stale-apps` would. Called here on rows
    made to look like production's, since a test database never had them.
    """
    import importlib

    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType

    assert not apps.is_installed("collection")
    for label, model in [("collection", "collection"), ("collection", "collectionitem"),
                         ("cards", "printing")]:
        ContentType.objects.create(app_label=label, model=model)

    migration = importlib.import_module("cards.migrations.0009_printings_out")
    migration.drop_stale_content_types(apps, None)

    assert not ContentType.objects.filter(app_label="collection").exists()
    assert not ContentType.objects.filter(app_label="cards", model="printing").exists()
    assert ContentType.objects.filter(app_label="cards", model="oraclecard").exists()


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
