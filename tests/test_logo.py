"""The inline logo and the favicon draw the same thing (phase 10 E).

`static/img/favicon.svg` stays the tab icon; `templates/core/_logo.html` is the
same picture with a class on each part, so CSS can colour it from the tokens
and move the fish. Two copies of one drawing drift apart the first time
somebody edits only one of them - this compares their shapes.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from django.template.loader import render_to_string

ROOT = Path(__file__).resolve().parent.parent
FAVICON = ROOT / "static" / "img" / "favicon.svg"
SVG = "{http://www.w3.org/2000/svg}"

#: What may differ: colour moved into CSS, names and wiring that only the
#: inline copy needs. Everything else is geometry and must match.
NOT_GEOMETRY = {"fill", "stroke", "class", "id", "clip-path", "aria-hidden", "focusable"}
#: Containers and labels: the inline copy groups the fish so it can move.
NOT_SHAPES = {"svg", "g", "title", "defs"}


def _shapes(svg_text: str) -> list[tuple[str, dict]]:
    root = ET.fromstring(svg_text)  # noqa: S314 - our own files, not user input
    shapes = []
    for element in root.iter():
        tag = element.tag.removeprefix(SVG)
        if tag in NOT_SHAPES:
            continue
        attributes = {k: v for k, v in element.attrib.items() if k not in NOT_GEOMETRY}
        shapes.append((tag, attributes))
    return shapes


def test_the_inline_logo_draws_the_favicon():
    inline = render_to_string("core/_logo.html", {"id": "test", "class": ""})

    assert _shapes(inline) == _shapes(FAVICON.read_text(encoding="utf-8"))


def test_two_logos_on_one_page_do_not_share_a_clip_path():
    first = render_to_string("core/_logo.html", {"id": "one"})
    second = render_to_string("core/_logo.html", {"id": "two"})

    assert 'id="one-flask"' in first
    assert 'id="two-flask"' in second
    assert "url(#one-flask)" in first


def test_the_logo_is_decoration():
    inline = render_to_string("core/_logo.html", {"id": "test"})

    assert 'aria-hidden="true"' in inline


def test_every_moving_part_stands_still_for_reduced_motion():
    css = (ROOT / "assets" / "css" / "input.css").read_text(encoding="utf-8")
    still = css[css.rindex("@media (prefers-reduced-motion: reduce)"):]

    for part in (".logo-animated .logo-fish", ".logo-animated .logo-bubble",
                 ".run-progress-fill", ".run-lines > li"):
        assert part in still, part
