"""Phase 9 H, the text pass: pages keep one sentence, the rest moved.

What moved has to still be reachable. Every "Why?" and every link into the
methodology page names an anchor, and an anchor that is not on the page drops
the reader at its top - the explanation is then as gone as if it had been
deleted.
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from billing import quotas, views

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

#: `{% include "core/_why.html" with anchor="x" %}` and `{% url 'methodology' %}#x`.
ANCHOR_REFERENCES = (
    re.compile(r'core/_why\.html"\s+with\s+anchor="([\w-]+)"'),
    re.compile(r"\{%\s*url\s+'methodology'\s*%\}#([\w-]+)"),
)


def _referenced_anchors() -> set[str]:
    anchors = set()
    for template in TEMPLATES.rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        for pattern in ANCHOR_REFERENCES:
            anchors.update(pattern.findall(text))
    return anchors


def test_the_pages_link_into_the_methodology_page():
    """Guards the guard: a regex that matched nothing would pass every page."""
    # Phase 10 took "opening" and "mulligan" off the run page with the opening
    # hands; the sections stay on the methodology page.
    assert {"reading", "report-milestones", "report-mana", "blind-spots",
            "games"} <= _referenced_anchors()


@pytest.mark.django_db
def test_every_anchor_a_page_links_to_is_on_the_methodology_page(client):
    content = client.get(reverse("methodology")).content.decode()
    missing = {a for a in _referenced_anchors() if f'id="{a}"' not in content}
    assert not missing


@pytest.mark.django_db
def test_the_usage_bar_is_capped_when_more_was_used_than_the_plan_allows(monkeypatch):
    """A plan changed mid-month can leave 30 used of a limit of 20."""
    user = get_user_model().objects.create_user(email="over@example.com", password="x" * 12)
    monkeypatch.setattr(quotas, "check", lambda *a, **k: quotas.QuotaDecision(
        allowed=False, metric="m", limit=20, used=30, remaining=0))

    rows = views._usage_rows(user)

    assert {row["pct"] for row in rows} == {100}


@pytest.mark.django_db
def test_each_limited_counter_gets_a_bar(client):
    user = get_user_model().objects.create_user(email="free@example.com", password="x" * 12)
    client.force_login(user)

    body = client.get(reverse("billing:plans")).content.decode()

    # Free limits decks and runs, so both have a bar here.
    assert body.count('role="progressbar"') == 2
