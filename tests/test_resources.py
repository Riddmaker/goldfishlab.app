"""Themen-Doc, Ressourcen: a static page of videos and sites.

What matters: it answers a stranger, every entry is shown, every link leaves
safely (https, a new tab, no referrer, no opener), nothing is embedded, and
the footer and the sitemap name it.
"""

import re

import pytest
from django.urls import reverse
from django.utils import translation

from core import resources

pytestmark = pytest.mark.django_db


def _page(client):
    response = client.get(reverse("resources"))
    assert response.status_code == 200
    return response.content.decode()


def _entries():
    return [item for groups in (resources.VIDEOS, resources.LINKS)
            for group in groups for item in group.items]


def test_a_stranger_sees_every_entry(client):
    body = _page(client)

    for item in _entries():
        assert item.url in body


def test_every_link_leaves_safely(client):
    body = _page(client)
    start = body.index("data-resources")
    section = body[start:body.index("</section>", start)]
    outside = re.findall(r'<a href="(https?://[^"]+)"([^>]*)>', section)

    assert len(outside) == len(_entries())
    for url, attributes in outside:
        assert url.startswith("https://"), url
        assert 'rel="noopener noreferrer"' in attributes, url
        assert 'target="_blank"' in attributes, url


def test_nothing_is_embedded(client):
    body = _page(client).lower()

    assert "<iframe" not in body
    assert "youtube-nocookie" not in body
    assert "ytimg" not in body


def test_the_footer_links_it(client):
    body = client.get(reverse("home")).content.decode()

    assert f'href="{reverse("resources")}"' in body


def test_the_sitemap_lists_it(client):
    body = client.get("/sitemap.xml").content.decode()

    assert reverse("resources") in body


def test_german_translates_our_words_not_the_titles(client, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    with translation.override("de"):
        url = reverse("resources")
    body = client.get(url).content.decode()

    assert "Ressourcen" in body
    assert "How To Play Commander in 27 Minutes" in body
