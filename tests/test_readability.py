"""Paket 3, PR 10 (Themen-Doc row 39): one sentence why, then "More".

What matters:

1. **No "Why?" any more.** The link into the methodology page is a quiet
   "More" - an offer to read on, not a question the reader has to answer.
2. **The sentence before it says why the section matters**, so the page can
   be understood without following the link.
3. **In every language**: "Mehr dazu" in German, through its own context, so
   the plain "More" elsewhere keeps its own translation.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse

from sharing import services as sharing
from tests.test_i18n import GERMAN, deck, finished_run, owner  # noqa: F401

pytestmark = pytest.mark.django_db

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
METHODOLOGY_LINK = re.compile(r'<a href="/[^"]*methodology/#[\w-]+"[^>]*>([^<]+)</a>')


def _links(body: str) -> set[str]:
    return {text.strip() for text in METHODOLOGY_LINK.findall(body)}


def test_no_template_asks_why_any_more():
    asking = [str(path.relative_to(TEMPLATES)) for path in TEMPLATES.rglob("*.html")
              if re.search(r'translate "Why\?"', path.read_text(encoding="utf-8"))]
    assert asking == []


def test_the_run_page_says_why_first_and_offers_more(client, owner, finished_run):  # noqa: F811
    client.force_login(owner)

    body = client.get(reverse("simulations:detail", args=[finished_run.pk])).content.decode()

    assert _links(body) == {"More"}, "every methodology link reads More"
    assert "How soon your deck gets going" in body
    assert "What your games start with" in body
    assert "The more it reads, the more the numbers tell." in body


def test_in_german_the_link_says_mehr_dazu(client, owner, finished_run, settings):  # noqa: F811
    settings.LANGUAGES = GERMAN
    client.force_login(owner)
    client.cookies["django_language"] = "de"

    body = client.get(reverse("simulations:detail", args=[finished_run.pk])).content.decode()

    assert _links(body) == {"Mehr dazu"}
    assert "Wie schnell dein Deck in Gang kommt" in body
    assert "Warum?" not in body


def test_a_shared_report_offers_more_too(client, finished_run):  # noqa: F811
    shared = sharing.share(finished_run)

    body = client.get(shared.get_absolute_url()).content.decode()

    assert _links(body) == {"More"}
    assert "The more it reads, the more the numbers tell." in body
