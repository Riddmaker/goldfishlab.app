"""Phase 0 acceptance: WCAG AA contrast on every declared token role pairing.

assets/css/input.css is the design-token source of truth; this test parses it
so a palette edit that breaks accessibility fails CI immediately.

This matters more here than in a normal project: parchment-on-ink is exactly
the kind of warm, low-chroma palette where contrast quietly fails while still
looking pleasant to the designer who picked it.
"""

import re
from pathlib import Path

import pytest

INPUT_CSS = Path(__file__).resolve().parent.parent / "assets" / "css" / "input.css"

# Anchors that must survive verbatim in the scales.
ANCHORS = {
    ("ink", 950): "#12100d",
    ("parchment", 100): "#f5ecd7",
    ("blood", 500): "#c43d33",
}

AA_NORMAL = 4.5
AA_LARGE = 3.0

# (description, foreground, background) - all must clear 4.5:1.
ROLE_PAIRINGS = [
    # Dark ground (the default page)
    ("body text on page", ("parchment", 100), ("ink", 950)),
    ("body text on raised panel", ("parchment", 100), ("ink", 900)),
    ("muted text on page", ("ink", 300), ("ink", 950)),
    ("heading on page", ("parchment", 200), ("ink", 950)),
    ("accent text on page", ("blood", 300), ("ink", 950)),
    ("success text on page", ("verdigris", 300), ("ink", 950)),
    # Parchment surfaces (cards, the playtest table, report panels)
    ("body text on parchment", ("ink", 900), ("parchment", 100)),
    ("body text on light parchment", ("ink", 900), ("parchment", 50)),
    ("muted text on parchment", ("ink", 700), ("parchment", 100)),
    ("link on parchment", ("blood", 700), ("parchment", 100)),
    ("success text on parchment", ("verdigris", 700), ("parchment", 100)),
    # Buttons
    ("primary button label", ("parchment", 100), ("blood", 700)),
    ("primary button hover label", ("parchment", 100), ("blood", 800)),
    ("secondary button label", ("ink", 900), ("parchment", 300)),
    ("success button label", ("parchment", 100), ("verdigris", 700)),
    # Badges / alerts
    ("danger badge", ("blood", 800), ("blood", 100)),
    ("success badge", ("verdigris", 800), ("verdigris", 100)),
    ("neutral badge", ("ink", 800), ("ink", 200)),
]


def _parse_scales() -> dict:
    """Read every `--color-<family>-<step>: #rrggbb;` declaration."""
    css = INPUT_CSS.read_text(encoding="utf-8")
    colors: dict = {}
    pattern = r"--color-([a-z]+)-(\d+):\s*(#[0-9a-fA-F]{6});"
    for family, step, value in re.findall(pattern, css):
        colors[(family, int(step))] = value.lower()
    return colors


SCALES = _parse_scales()


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (
        0.2126 * _srgb_to_linear(r)
        + 0.7152 * _srgb_to_linear(g)
        + 0.0722 * _srgb_to_linear(b)
    )


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two hex colours."""
    lighter, darker = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _resolve(token) -> str:
    if isinstance(token, str):
        return {"white": "#ffffff", "black": "#000000"}[token]
    assert token in SCALES, f"token {token} not declared in input.css"
    return SCALES[token]


def test_input_css_exists():
    assert INPUT_CSS.is_file(), f"missing {INPUT_CSS}"


def test_scales_parsed():
    """Guard against the regex silently matching nothing."""
    assert len(SCALES) >= 40, f"only parsed {len(SCALES)} colour tokens"


@pytest.mark.parametrize(("token", "expected"), list(ANCHORS.items()))
def test_anchor_colors_survive(token, expected):
    """The three anchors define the brand; a scale edit must not drift them."""
    assert SCALES.get(token) == expected


@pytest.mark.parametrize(
    ("description", "fg", "bg"),
    ROLE_PAIRINGS,
    ids=[p[0].replace(" ", "-") for p in ROLE_PAIRINGS],
)
def test_role_pairings_meet_aa(description, fg, bg):
    ratio = contrast_ratio(_resolve(fg), _resolve(bg))
    assert ratio >= AA_NORMAL, (
        f"{description}: {_resolve(fg)} on {_resolve(bg)} is {ratio:.2f}:1, needs {AA_NORMAL}:1"
    )


def test_large_text_pairings_meet_aa_large():
    """Display-size headings may use the 3:1 threshold."""
    ratio = contrast_ratio(_resolve(("parchment", 300)), _resolve(("ink", 950)))
    assert ratio >= AA_LARGE
