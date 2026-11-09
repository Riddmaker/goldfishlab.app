"""The weeks of news as the site shows them (PR 22).

The files are `news/issues/*.json`, checked by `news.rules` - here they are
read once per process and handed out in the reader's language. A week whose
`published` day has not come yet waits for it, like a changelog entry.
"""

from dataclasses import dataclass
from datetime import date
from functools import cache

from django.utils import timezone
from django.utils.translation import get_language

from news import rules
from news.check import FOLDER


@dataclass(frozen=True)
class Link:
    source: str
    url: str
    title: str


@dataclass(frozen=True)
class Item:
    slug: str
    headline: str
    summary: str
    links: tuple[Link, ...]
    #: The item's anchor on /news/ and the last part of its feed id.
    anchor: str


@dataclass(frozen=True)
class Issue:
    week: str
    published: date
    items: tuple[Item, ...]

    @property
    def anchor(self) -> str:
        return self.week.lower()

    @property
    def number(self) -> int:
        return int(self.week.rsplit("W", 1)[1])


@cache
def _all() -> list[dict]:
    return rules.load(FOLDER)


def _language(code: str | None) -> str:
    code = (code or "en").lower()
    return code if code in rules.LANGUAGES else "en"


def _shown(raw: dict, language: str) -> Issue:
    items = tuple(
        Item(
            slug=item["slug"],
            headline=item["headline"][language],
            summary=item["summary"][language],
            links=tuple(Link(rules.SOURCES[link["source"]].name, link["url"], link["title"])
                        for link in item["links"]),
            anchor=f"{raw['week'].lower()}-{item['slug']}",
        )
        for item in raw["items"]
    )
    return Issue(raw["week"], raw["published"], items)


def published(today: date | None = None, language: str | None = None) -> list[Issue]:
    """Every week out by `today`, newest first, in `language` (the active
    one by default)."""
    today = today or timezone.localdate()
    language = _language(language or get_language())
    return [_shown(raw, language) for raw in _all() if raw["published"] <= today]


def latest(today: date | None = None, language: str | None = None) -> Issue | None:
    issues = published(today, language)
    return issues[0] if issues else None
