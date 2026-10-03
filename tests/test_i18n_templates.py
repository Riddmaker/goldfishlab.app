"""Phase 12 B: no visible English outside a translate tag.

A string that is not marked never reaches a catalogue, and nothing else notices:
the page renders, in English, in the middle of a German one. This test reads
every template the way Django does (its own lexer) and then the HTML the way a
browser shows it: text nodes and the attributes a person sees or hears (`alt`,
`title`, `placeholder`, `aria-label`, a button's `value`, the page description).
An element with its own `lang` says it is in that language on purpose and is
skipped (the Fan Content notice stays English).

What it does not see: text built in Python and handed to the template as a
variable. That is the Python half of the batch, checked in its own tests.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template.base import Lexer, TokenType

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

#: Not translated in this batch, each for a reason.
LATER = {
    # Q4 (b): the legal pages stay English with a translated note on top (H).
    "core/privacy.html": "H",
    "core/terms.html": "H",
    "core/imprint.html": "H",
    "core/methodology.html": "H",
    # Only with DEBUG; nobody but us sees it.
    "core/styleguide.html": "dev only",
}
#: Plain-text templates read too: the mails' text halves, subjects and allauth
#: messages are text a person reads (phase 12 C).
TEXT_DIRS = ("account/",)

#: Names, not words: the same in every language.
NAMES = {"Goldfish Lab", "Goldfish\xa0Lab", "Scryfall", "Commander Spellbook", "Moxfield",
         "Archidekt", "ManaBox", "Stripe", "Mistral", "AGPL"}

#: A tag or a variable: something that is not text written in the template.
HOLE = ""
#: Tags whose whole content is skipped (a translate block is already marked).
SKIPPED = {"comment": "endcomment", "blocktranslate": "endblocktranslate",
           "blocktrans": "endblocktrans", "verbatim": "endverbatim"}
WORD = re.compile(r"[^\W\d_]{2,}")
VISIBLE_ATTRIBUTES = {"alt", "title", "placeholder", "aria-label", "aria-description",
                      "aria-valuetext", "aria-roledescription"}


def skeleton(source: str) -> str:
    """The template's own text, with every tag and variable a hole."""
    parts, until = [], None
    for token in Lexer(source).tokenize():
        if until:
            if token.token_type == TokenType.BLOCK and token.split_contents()[0] == until:
                until = None
                parts.append(HOLE)
            continue
        if token.token_type == TokenType.TEXT:
            parts.append(token.contents)
        elif token.token_type == TokenType.BLOCK:
            until = SKIPPED.get(token.split_contents()[0])
            parts.append(HOLE)
        elif token.token_type == TokenType.VAR:
            parts.append(HOLE)
    return "".join(parts)


#: Elements without an end tag, so they never open a level.
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
        "track", "wbr"}


class _Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found, self._open = [], []

    @property
    def _hidden(self):
        return any(hidden for _, hidden in self._open)

    def _check(self, text):
        text = " ".join(text.replace(HOLE, " ").split())
        words = text
        for name in NAMES:
            words = words.replace(name, " ")
        if WORD.search(words):
            self.found.append(text)

    def handle_starttag(self, tag, attrs):
        attrs = {name: value or "" for name, value in attrs}
        if tag not in VOID:
            # `<html lang>` names the page's language; any other `lang` marks an
            # element kept in a language of its own.
            own_language = "lang" in attrs and tag != "html"
            self._open.append((tag, tag in ("script", "style") or own_language))
        if self._hidden:
            return
        for name in VISIBLE_ATTRIBUTES & attrs.keys():
            self._check(attrs[name])
        if tag == "input" and attrs.get("type") in ("submit", "button", "reset"):
            self._check(attrs.get("value", ""))
        if tag == "meta" and attrs.get("name") == "description":
            self._check(attrs.get("content", ""))

    def handle_endtag(self, tag):
        # Close up to the matching tag; an unclosed <p> or <li> closes with it.
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                del self._open[index:]
                break

    def handle_data(self, data):
        if not self._hidden:
            self._check(data)


def untranslated(source: str) -> list[str]:
    reader = _Reader()
    reader.feed(skeleton(source))
    reader.close()
    return reader.found


def _checked():
    for path in sorted(TEMPLATES.rglob("*")):
        name = path.relative_to(TEMPLATES).as_posix()
        text = path.suffix == ".txt" and name.startswith(TEXT_DIRS)
        if (path.suffix == ".html" or text) and name not in LATER:
            yield name


def test_the_checker_finds_text_and_ignores_what_is_marked():
    """Guard against a checker that can never fail."""
    assert untranslated("<p>Hello {{ name }}</p>") == ["Hello"]
    assert untranslated('<img alt="A fish" src="x">') == ["A fish"]
    assert untranslated('<button aria-label="Close">×</button>') == ["Close"]
    assert untranslated("{% if a %}games{% endif %}") == ["games"]
    assert untranslated('<meta name="description" content="Plays decks">') == ["Plays decks"]
    marked = """{% load i18n %}<p>{% translate "Hello" %} {{ name }}</p>
        {% comment %}Notes for us.{% endcomment %}{# and here #}
        <p>{% blocktranslate trimmed count n=n %}one{% plural %}many{% endblocktranslate %}</p>
        <script>const x = "text";</script><p class="mt-2 text-sm">Goldfish Lab · 42 %</p>
        <p lang="en">Stays <a href="/">English</a>.</p>"""
    assert untranslated(marked) == []
    assert untranslated('<p lang="en">Here</p><p>Not here</p>') == ["Not here"]
    # The root's `lang` is the page's own, not an exception (base.html went
    # unchecked in B1 because of it).
    assert untranslated('<html lang="en"><p>Read</p></html>') == ["Read"]


@pytest.mark.parametrize("name", list(_checked()))
def test_no_template_shows_text_outside_a_translate_tag(name):
    assert untranslated((TEMPLATES / name).read_text(encoding="utf-8")) == []
