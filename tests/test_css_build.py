"""The compiled CSS must know about every class the templates use.

This exists because of a bug that nothing else could see. The deck templates
were added after `main.css` was last built, so Tailwind had never scanned them
and never generated their utilities. Every test passed, every page answered
HTTP 200, and the deck list rendered with its labels jammed together because
`gap-x-4` did not exist in the stylesheet.

Tailwind only emits the classes it finds by scanning source files, so **adding
a template is a CSS change**. That is easy to forget and invisible afterwards,
which is exactly the kind of thing that belongs in a test rather than in
somebody's memory.

`static/css/main.css` is a build artefact and is gitignored, so these tests
skip rather than fail when it has not been built yet - a fresh checkout has no
stylesheet and that is not an error.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAIN_CSS = ROOT / "static" / "css" / "main.css"
TEMPLATES = ROOT / "templates"

#: `class="..."` in a Django template, including multi-line attributes.
_CLASS_ATTR = re.compile(r'class="([^"]*)"', re.DOTALL)

# Classes a template may legitimately use that Tailwind will not emit:
# hand-written rules from input.css, and Django's own form markup.
HAND_WRITTEN = {
    "surface-parchment",
    "tabular",
    "auth-form",
    "errorlist",
    "form-group",
    # htmx's, owned by input.css since Phase 8 - see the CSP tests below.
    "htmx-indicator",
    "htmx-request",
}

# Prefixes whose utilities are generated with a different literal spelling than
# the one written in the template (variants, arbitrary values, fractions).
SKIP_PREFIXES = ("hover:", "focus:", "sm:", "md:", "lg:", "focus-visible:", "last:")


#: Pages inside the Django admin (P2's stats page): styled by the admin's own
#: stylesheet, not by main.css.
ADMIN = TEMPLATES / "admin"


def _used_classes() -> set[str]:
    classes: set[str] = set()
    for template in TEMPLATES.rglob("*.html"):
        if template.is_relative_to(ADMIN):
            continue
        for attribute in _CLASS_ATTR.findall(template.read_text(encoding="utf-8")):
            # Skip anything holding a template tag; the rendered value is not
            # knowable from the source.
            if "{" in attribute:
                continue
            classes.update(attribute.split())
    return classes


@pytest.fixture(scope="module")
def stylesheet() -> str:
    if not MAIN_CSS.exists():
        pytest.skip("main.css has not been built; run .bin/tailwindcss")
    return MAIN_CSS.read_text(encoding="utf-8")


def test_every_template_class_exists_in_the_compiled_css(stylesheet):
    """The guard for "I added a template and forgot to rebuild the CSS"."""
    missing = sorted(
        name
        for name in _used_classes()
        if name not in HAND_WRITTEN
        and not name.startswith(SKIP_PREFIXES)
        # Tailwind escapes special characters in selectors (`.p-1\.5`), so the
        # class is searched for by its escaped form.
        and _escape(name) not in stylesheet
    )
    assert not missing, (
        "these classes are used in templates but absent from main.css - "
        "rebuild it with `.bin/tailwindcss -i assets/css/input.css -o static/css/main.css`: "
        f"{missing}"
    )


def _escape(name: str) -> str:
    """How Tailwind writes a class name inside a CSS selector."""
    escaped = name
    for character in ".:/[]%":
        escaped = escaped.replace(character, "\\" + character)
    return "." + escaped


def test_the_stylesheet_is_actually_populated(stylesheet):
    """A truncated or failed build produces a file, just an unusable one."""
    assert len(stylesheet) > 10_000
    assert "--color-parchment-100" in stylesheet


def test_no_template_comment_spans_more_than_one_line():
    """`{# ... #}` is a SINGLE-LINE comment. Across lines it is just text.

    Django's `{# #}` does not span lines. A comment written across three of
    them is not a comment at all: the whole block renders into the page, in
    full, for every visitor. It happened in `base.html`, so it appeared at the
    top of every single page - and nothing caught it. Every view test passed,
    every page answered HTTP 200, djlint was clean, and the explanation of why
    htmx is self-hosted was sitting above the site header in the browser.

    The rule is simple enough to check statically: a `{#` has to be closed on
    the line it opens. Anything longer belongs in `{% comment %}`.
    """
    offenders = []
    for template in TEMPLATES.rglob("*.html"):
        for number, line in enumerate(
            template.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "{#" in line and "#}" not in line[line.index("{#"):]:
                offenders.append(f"{template.relative_to(ROOT)}:{number}")

    assert not offenders, (
        "these `{# ... #}` comments are not closed on their own line, so they "
        "render as visible text - use {% comment %} instead:\n  "
        + "\n  ".join(offenders)
    )


# --- Inline <style> elements, and why there are none -------------------------
#
# Phase 8 put a Content-Security-Policy on every response with `style-src
# 'self'` and no `'unsafe-inline'`. Inline style ATTRIBUTES are allowed through
# `style-src-attr`, because every chart here is a server-rendered div whose
# width is the datum. Inline <style> ELEMENTS are not, and cannot be: a nonce
# would have to be threaded through every template and would buy nothing.
#
# These are static checks for the same reason the rest of this file is static:
# the bug they pin answered HTTP 200 on every page and was visible only in the
# browser's console.

_STYLE_ELEMENT = re.compile(r"<style[\s>]", re.IGNORECASE)
_DJANGO_COMMENT = re.compile(
    r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}|\{#.*?#\}", re.DOTALL
)


def _without_comments(template) -> str:
    """Template source with its comments removed.

    Necessary rather than tidy, and this test found out the hard way: the
    comment in `base.html` explaining why there are no inline <style> elements
    contains the words `<style>`, and matched itself. A commented-out element
    is not in the page either, so stripping comments is the correct reading of
    the question, not a way round it.
    """
    return _DJANGO_COMMENT.sub("", template.read_text(encoding="utf-8"))


def test_no_template_carries_an_inline_style_element():
    """`style-src 'self'` refuses one, and the page still answers HTTP 200.

    A <style> block added to a template would be silently dropped by the
    browser - the page renders, looks slightly wrong, and nothing fails.
    """
    offenders = [
        str(template.relative_to(ROOT))
        for template in TEMPLATES.rglob("*.html")
        if _STYLE_ELEMENT.search(_without_comments(template))
    ]
    assert not offenders, (
        f"These templates carry an inline <style> element, which the "
        f"Content-Security-Policy refuses: {offenders}. Put the rules in "
        f"assets/css/input.css instead."
    )


def test_htmx_is_told_not_to_inject_its_indicator_styles():
    """The bug this pins cost a screenshot pass to find.

    htmx writes its two `.htmx-indicator` rules into a <style> element in
    <head> on load. Under our CSP the browser refuses it, and **every page in
    the application** logged a violation - while still answering HTTP 200 and
    looking completely correct, because nothing uses the class yet. The first
    loading spinner somebody added months later would simply not have worked,
    with nothing pointing here.

    Turning the injection off is better than whitelisting htmx's style hash:
    there is then no inline style element anywhere, and nothing to re-hash when
    htmx is upgraded.
    """
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "htmx-config" in base
    assert '"includeIndicatorStyles": false' in base


def test_the_indicator_rules_survive_being_taken_off_htmx(stylesheet):
    """Disabling the injection is only safe if we serve the rules ourselves.

    Otherwise `class="htmx-indicator"` becomes a class that does nothing, which
    is a worse bug than the one being fixed - an indicator that is permanently
    visible rather than one that never appears.
    """
    assert ".htmx-indicator" in stylesheet
    assert ".htmx-request" in stylesheet
