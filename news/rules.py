"""What a week's news file must be (PR 22). Plain Python, no Django.

The weekly news is written by a Claude routine, which opens a pull request
with one new file, `news/issues/<YYYY>-W<ww>.json`. The owner reads it and
merges it; nothing is published unreviewed. These rules are what can be
checked by a program, so the owner only has to read for sense:

* **Only the sources below,** each on its own host and over https. No other
  link, and never Wizards of the Coast, Scryfall or Reddit.
* **Our words, not theirs.** Each item has a headline and two or three
  sentences in every language of the site, short, without markup. The
  original title appears only as the text of the link to it.
* **Facts that can be found.** Each item names two to five `facts` - card
  names, dates, numbers, a set - that the English text has to use. With the
  texts it was written from at hand (`check --sources`: the article where the
  source allows opening it, else what its feed supplied), each fact has to
  stand in one of them, word for word, and no run of eight words may be
  copied from them.

`news.check` runs these over every file and is what the routine and CI run;
`news.issues` reads the same files for the site.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Source:
    name: str
    host: str
    feed: str
    #: May the routine open the article itself? Where not, it writes from
    #: what the feed supplies, and the facts are checked against that.
    articles: bool = False


#: The sources the routine reads, and the only hosts a news item may link.
#: Each site's robots.txt and terms were read on 2026-11-08
#: (docs/research/paket5/news-sources.html, news-sources-more.html, and on
#: 2026-11-09 news-sources-new.html and news-cardkingdom-tos.html); most
#: forbid automated reading, so this list is short on purpose. A source is
#: added only after its terms allow it.
SOURCES = {
    # Its terms exempt "use of the Website's RSS feed(s) with the content
    # supplied via those feeds", with a link to the article; articles no.
    "wargamer": Source("Wargamer", "www.wargamer.com",
                       "https://www.wargamer.com/magic-the-gathering/rss"),
}

#: The site's languages, English first (`settings.LANGUAGE_NAMES`; a test
#: keeps the two the same).
LANGUAGES = ("en", "de", "fr", "it", "es", "pt-br", "ja")

#: Never linked, never named as a host in the text: the owner's standing rule
#: for Wizards and Scryfall, and Reddit's terms (commercial use only under a
#: contract; its RSS ends on 2026-11-13, docs/research/paket5/news-reddit.html).
FORBIDDEN = ("wizards.com", "scryfall.com", "reddit.com", "redd.it")

ITEMS = (1, 8)
SOURCES_PER_ITEM = (1, 3)
FACTS = (2, 5)
HEADLINE = 90
SUMMARY = (60, 480)
#: Japanese says as much in far fewer characters.
DENSE = {"ja": (25, 240)}
FACT = 60
TITLE = 200
COPIED_WORDS = 8

WEEK = re.compile(r"(\d{4})-W(\d{2})")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MARKUP = re.compile(r"[<>]|https?://|www\.", re.IGNORECASE)


class Invalid(ValueError):
    """A file that breaks a rule. The message says which and where."""


def week_of(name: str) -> tuple[int, int]:
    match = WEEK.fullmatch(name)
    if not match:
        raise Invalid(f"{name}: a week is written 2026-W46")
    year, week = int(match[1]), int(match[2])
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as error:
        raise Invalid(f"{name}: no such week") from error
    return year, week


def normal(text: str) -> str:
    """Lower case, one kind of apostrophe and dash, single spaces: how a fact
    and an article are compared."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[‘’ʼ`´]", "'", text)
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", normal(text))


def _text(where: str, value, limit: tuple[int, int] | int) -> str:
    low, high = limit if isinstance(limit, tuple) else (1, limit)
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{where}: missing")
    if value != value.strip() or "\n" in value:
        raise Invalid(f"{where}: one line, no spaces at either end")
    if not low <= len(value) <= high:
        raise Invalid(f"{where}: {len(value)} characters, allowed {low} to {high}")
    if MARKUP.search(value):
        raise Invalid(f"{where}: no markup and no addresses in the text")
    lowered = value.casefold()
    for host in FORBIDDEN:
        if host in lowered:
            raise Invalid(f"{where}: names {host}")
    return value


def _languages(where: str, value, limit, dense=None) -> dict:
    if not isinstance(value, dict) or set(value) != set(LANGUAGES):
        raise Invalid(f"{where}: needs exactly the languages {', '.join(LANGUAGES)}")
    dense = dense or {}
    return {code: _text(f"{where}.{code}", value[code], dense.get(code, limit))
            for code in LANGUAGES}


def _link(where: str, value) -> dict:
    if not isinstance(value, dict) or set(value) != {"source", "url", "title"}:
        raise Invalid(f"{where}: a link is source, url and title")
    source = SOURCES.get(value["source"])
    if source is None:
        raise Invalid(f"{where}: unknown source {value['source']!r}")
    url = value["url"]
    if not isinstance(url, str) or len(url) > 300 or any(c.isspace() for c in url):
        raise Invalid(f"{where}: not an address")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != source.host or parts.port \
            or parts.username or len(parts.path) < 2:
        raise Invalid(f"{where}: must be an https page on {source.host}")
    title = value["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE \
            or re.search(r"[<>]", title):
        raise Invalid(f"{where}: the original title, as the link's text, up to {TITLE} "
                      "characters")
    return {"source": value["source"], "url": url, "title": title.strip()}


def _item(where: str, value) -> dict:
    keys = {"slug", "headline", "summary", "facts", "links"}
    if not isinstance(value, dict) or set(value) != keys:
        raise Invalid(f"{where}: an item is {', '.join(sorted(keys))}")
    slug = value["slug"]
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or len(slug) > 60:
        raise Invalid(f"{where}.slug: lower case words joined by dashes")
    headline = _languages(f"{where}.headline", value["headline"], HEADLINE)
    summary = _languages(f"{where}.summary", value["summary"], SUMMARY, DENSE)
    links = value["links"]
    if not isinstance(links, list) or not SOURCES_PER_ITEM[0] <= len(links) <= SOURCES_PER_ITEM[1]:
        raise Invalid(f"{where}.links: {SOURCES_PER_ITEM[0]} to {SOURCES_PER_ITEM[1]} links")
    links = [_link(f"{where}.links[{n}]", link) for n, link in enumerate(links)]
    if len({link["url"] for link in links}) != len(links):
        raise Invalid(f"{where}.links: the same link twice")
    facts = value["facts"]
    if not isinstance(facts, list) or not FACTS[0] <= len(facts) <= FACTS[1]:
        raise Invalid(f"{where}.facts: {FACTS[0]} to {FACTS[1]} facts")
    facts = [_text(f"{where}.facts[{n}]", fact, (2, FACT)) for n, fact in enumerate(facts)]
    english = normal(headline["en"] + " " + summary["en"])
    for fact in facts:
        if normal(fact) not in english:
            raise Invalid(f"{where}.facts: {fact!r} is not in the English text")
    return {"slug": slug, "headline": headline, "summary": summary,
            "facts": facts, "links": links}


def parse(name: str, data) -> dict:
    """One week's file, checked. `name` is the file's name without `.json`."""
    year, week = week_of(name)
    if not isinstance(data, dict) or set(data) != {"week", "published", "items"}:
        raise Invalid(f"{name}: a week is week, published and items")
    if data["week"] != name:
        raise Invalid(f"{name}: says it is week {data['week']!r}")
    try:
        published = date.fromisoformat(data["published"])
    except (TypeError, ValueError) as error:
        raise Invalid(f"{name}.published: a date, 2026-11-10") from error
    if published.isocalendar()[:2] != (year, week):
        raise Invalid(f"{name}.published: {published} is not in week {name}")
    items = data["items"]
    if not isinstance(items, list) or not ITEMS[0] <= len(items) <= ITEMS[1]:
        raise Invalid(f"{name}.items: {ITEMS[0]} to {ITEMS[1]} items")
    items = [_item(f"{name}.items[{n}]", item) for n, item in enumerate(items)]
    slugs = [item["slug"] for item in items]
    if len(set(slugs)) != len(slugs):
        raise Invalid(f"{name}.items: the same slug twice")
    return {"week": name, "published": published, "items": items}


def load(folder: Path) -> list[dict]:
    """Every week in `folder`, checked, newest first. A link may be news only
    once: the same address in two weeks is refused."""
    issues = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise Invalid(f"{path.name}: not JSON ({error})") from error
        issues.append(parse(path.stem, data))
    seen = {}
    for issue in issues:
        for item in issue["items"]:
            for link in item["links"]:
                if link["url"] in seen:
                    raise Invalid(f"{issue['week']}.{item['slug']}: {link['url']} was "
                                  f"already news in {seen[link['url']]}")
                seen[link["url"]] = issue["week"]
    return sorted(issues, key=lambda issue: issue["published"], reverse=True)


def against(issue: dict, articles: Path) -> list[str]:
    """Check one week against the texts it was written from. `articles`
    holds `<slug>-<n>.txt` for the item's n-th link (from 1): the article, or
    the feed's text for it where the source may not be opened. The routine
    keeps them outside the repository. The problems found, or none."""
    problems = []
    for item in issue["items"]:
        texts = []
        for n, _link in enumerate(item["links"], start=1):
            path = articles / f"{item['slug']}-{n}.txt"
            if not path.is_file():
                problems.append(f"{item['slug']}: no article text {path.name}")
                continue
            texts.append(path.read_text(encoding="utf-8"))
        if not texts:
            continue
        article = normal(" ".join(texts))
        for fact in item["facts"]:
            if normal(fact) not in article:
                problems.append(f"{item['slug']}: the fact {fact!r} is not in the article")
        source_runs = _runs(words(" ".join(texts)))
        for code in ("en", *LANGUAGES[1:]):
            text = item["headline"][code] + " " + item["summary"][code]
            for run in _runs(words(text)):
                if run in source_runs:
                    problems.append(f"{item['slug']}.{code}: copied from the article: "
                                    f"{' '.join(run)!r}")
                    break
    return problems


def _runs(tokens: list[str]) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + COPIED_WORDS]) for i in range(len(tokens) - COPIED_WORDS + 1)}
