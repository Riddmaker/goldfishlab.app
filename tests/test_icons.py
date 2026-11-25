"""Our symbols (docs/DESIGN.html, SYMBOLS; Themen-Doc, Paket 4, PR 15).

`{% icon %}` sets a drawing from core/icons/ inline. These hold the folder, the
tag, the templates that use it, the stylesheet and the styleguide together, so
a symbol cannot be used without being drawn, drawn without being documented,
or move for somebody who asked for less motion.
"""

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from django.template import Context, Template

from cards.themes import THEMES, TYPAL
from core import symbols
from core.templatetags import icons
from scripts.build_styleguide import SYMBOLS
from simulation.cards import CARD_TYPES
from simulations.deck_cards import Bar
from simulations.report import SEEN_MECHANIC_ROLES, SEEN_ROLES, SEEN_STRATEGY_ROLES

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
INPUT_CSS = ROOT / "assets" / "css" / "input.css"
SVG = "{http://www.w3.org/2000/svg}"


def _render(source: str, **context) -> str:
    return Template("{% load icons %}" + source).render(Context(context))


def _icon_block() -> str:
    css = INPUT_CSS.read_text(encoding="utf-8")
    return css[css.index("/* icons:start"):css.index("/* icons:end */")]


@pytest.mark.parametrize("name", sorted(icons.NAMES))
def test_every_symbol_renders_as_hidden_svg(name):
    root = ET.fromstring(_render(f'{{% icon "{name}" %}}'))  # noqa: S314 - our own files

    assert root.tag.removeprefix(SVG) == "svg"
    assert root.get("viewBox") == "0 0 32 32"
    assert root.get("aria-hidden") == "true"
    assert root.get("focusable") == "false"
    assert f"icon-{name}" in root.get("class").split()
    assert len(root) > 0


def test_large_symbols_say_so():
    assert "icon-lg" in _render('{% icon "speed" size="lg" %}')


def test_an_unknown_name_renders_nothing_and_says_so(caplog):
    with caplog.at_level(logging.WARNING, logger="core.templatetags.icons"):
        assert _render('{% icon "nope" %}') == ""
        assert _render('{% icon "speed" size="huge" %}') == ""
    assert "nope" in caplog.text


def test_every_name_a_template_uses_is_drawn():
    used = set()
    for path in TEMPLATES.rglob("*.html"):
        used |= set(re.findall(r'\{%\s*icon\s+"([a-z-]+)"', path.read_text(encoding="utf-8")))
    assert used, "no template uses {% icon %}"
    assert used <= icons.NAMES, used - icons.NAMES


def test_every_role_theme_and_type_has_a_drawn_symbol():
    """Deck page v2, PR C: one vocabulary, each word with its drawing."""
    roles = {key for key, _ in SEEN_ROLES + SEEN_STRATEGY_ROLES + SEEN_MECHANIC_ROLES}
    assert roles == set(symbols.ROLE)
    assert {theme.key for theme in THEMES} | {TYPAL} == set(symbols.THEME)
    assert set(CARD_TYPES) == set(symbols.TYPE)
    drawn = set(symbols.ROLE.values()) | set(symbols.THEME.values()) | set(symbols.TYPE.values())
    assert drawn <= icons.NAMES, drawn - icons.NAMES


@pytest.mark.parametrize(("param", "value", "expected"), [
    ("cat", "ramp", "ramp"),
    ("cat", "wipe", "wipe"),
    ("type", "creature", "creature"),
    ("type", "draw", ""),
    ("cat", "nope", ""),
])
def test_a_bar_has_the_symbol_of_its_role_or_type(param, value, expected):
    bar = Bar(key="k", label="L", count=1, param=param, value=value, width=1.0,
              band=None, band_label="")
    assert bar.icon == expected


def test_no_name_is_no_symbol_and_no_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert _render('{% icon "" %}') == ""
    assert not caplog.records


def test_the_styleguide_shows_every_symbol_once():
    assert {name for name, _, _ in SYMBOLS} == icons.NAMES
    assert len(SYMBOLS) == len(icons.NAMES)


def test_every_class_a_drawing_uses_is_styled():
    block = _icon_block()
    for name in icons.NAMES:
        for found in re.findall(r'class="([^"]+)"', icons.shapes(name)):
            for cls in found.split():
                assert f".{cls}" in block, f"{name}: .{cls} has no rule"


def test_no_drawing_carries_a_colour_of_its_own():
    """Colours come from the tokens via classes, never an inline hex."""
    for name in icons.NAMES:
        assert "#" not in icons.shapes(name), name


def test_every_move_waits_for_no_motion_preference():
    """Each transition and animation sits inside the no-preference block."""
    block = _icon_block()
    start = block.index("@media (prefers-reduced-motion: no-preference)")
    inside = block[start:block.index("@keyframes")]
    outside = block[:start] + block[block.index("@keyframes"):]
    assert "animation:" in inside and "transition:" in inside
    assert "animation:" not in outside
    assert "transition:" not in outside
