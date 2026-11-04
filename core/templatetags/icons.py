"""`{% icon "speed" %}`: one of our symbols, inline (Themen-Doc, Paket 4, PR 15).

The drawings live in `core/icons/<name>.svg`, shapes only, so the styleguide
builder reads the very same files. Inline rather than an `<img>` or a sprite's
`<use>`: CSS has to reach the parts - the sand, the fan, the seal - to colour
and move them, and it cannot reach into either of those.

Info symbols only (DESIGN.md, SYMBOLS): decorative, hidden from screen readers,
always beside the word that says the same. The functional icons - the pencil,
the bin, the padlock - keep their own templates and their own look.

The name always comes from a template, never from a request. An unknown one
renders nothing and is logged, rather than taking the page down with it;
tests/test_icons.py makes sure every name a template uses exists.
"""

import functools
import logging
from pathlib import Path

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()
logger = logging.getLogger(__name__)

ICONS_DIR = Path(__file__).resolve().parent.parent / "icons"
NAMES = frozenset(path.stem for path in ICONS_DIR.glob("*.svg"))
SIZES = {"": "", "lg": " icon-lg"}


@functools.cache
def shapes(name: str) -> str:
    """The inside of one drawing: our own file, read once."""
    return (ICONS_DIR / f"{name}.svg").read_text(encoding="utf-8").strip()


@register.simple_tag
def icon(name: str, size: str = "") -> str:
    """The symbol `name`, 1.25em beside text or `size="lg"` (2.5rem)."""
    if name not in NAMES or size not in SIZES:
        logger.warning("Unknown icon %r (size %r)", name, size)
        return ""
    return format_html(
        '<svg class="icon icon-{}{}" viewBox="0 0 32 32" fill="none" stroke="currentColor"'
        ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"'
        ' aria-hidden="true" focusable="false">{}</svg>',
        name,
        SIZES[size],
        mark_safe(shapes(name)),  # noqa: S308 - our own files, see the docstring
    )
