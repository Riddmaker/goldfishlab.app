"""The Discord and Ko-fi links in the footer (C1, M3)."""

import pytest
from django.urls import reverse

from goldfishlab.settings.base import https_or_blank

DISCORD = "https://discord.gg/goldfishlab"
KOFI = "https://ko-fi.com/goldfishlab"


@pytest.mark.parametrize(("given", "kept"), [
    (DISCORD, DISCORD),
    (f"  {KOFI} ", KOFI),
    ("", ""),
    ("http://ko-fi.com/goldfishlab", ""),
    ("javascript:alert(1)", ""),
    ("https://", ""),
])
def test_only_an_https_address_is_kept(given, kept):
    assert https_or_blank(given) == kept


@pytest.mark.django_db
def test_both_links_show_once_set(client, settings):
    settings.DISCORD_URL, settings.KOFI_URL = DISCORD, KOFI
    body = client.get(reverse("home")).content.decode()
    assert f'href="{DISCORD}"' in body
    assert f'href="{KOFI}"' in body
    assert "Support us on Ko-fi" in body


@pytest.mark.django_db
def test_no_link_while_blank(client, settings):
    settings.DISCORD_URL = settings.KOFI_URL = ""
    body = client.get(reverse("home")).content.decode()
    assert "discord.gg" not in body
    assert "ko-fi.com" not in body
    assert "Support us on Ko-fi" not in body


@pytest.mark.django_db
def test_the_link_is_translated(client, settings):
    settings.KOFI_URL = KOFI
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    client.cookies["django_language"] = "de"
    body = client.get(reverse("home"), follow=True).content.decode()
    assert "Unterstütze uns auf Ko-fi" in body
