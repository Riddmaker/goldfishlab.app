"""Phase 0b acceptance: the design documents exist and are in sync.

DESIGN.md declares the rules, STYLEGUIDE.html renders them, input.css holds
the tokens. The sync rule says decisions flow DESIGN.md -> STYLEGUIDE.html ->
input.css. A rule is only worth writing down if something checks it, so these
tests make a stale or contradictory styleguide fail CI.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "DESIGN.md"
STYLEGUIDE = ROOT / "STYLEGUIDE.html"
INPUT_CSS = ROOT / "assets" / "css" / "input.css"
BUILDER = ROOT / "scripts" / "build_styleguide.py"
FONT_DIR = ROOT / "static" / "fonts"

FAMILIES = ["ink", "parchment", "blood", "verdigris"]


def test_design_md_exists():
    assert DESIGN.is_file()


def test_styleguide_exists():
    assert STYLEGUIDE.is_file()


@pytest.mark.parametrize("family", FAMILIES)
def test_every_family_is_documented_in_design_md(family):
    """A colour family that exists in CSS but not in DESIGN.md is undocumented."""
    assert family in DESIGN.read_text(encoding="utf-8")


@pytest.mark.parametrize("family", FAMILIES)
def test_every_documented_family_exists_in_css(family):
    """And a family documented but not defined is a broken promise."""
    css = INPUT_CSS.read_text(encoding="utf-8")
    assert f"--color-{family}-" in css


def test_no_undocumented_colour_families():
    """The reverse direction: nothing sneaks into the palette undocumented."""
    css = INPUT_CSS.read_text(encoding="utf-8")
    found = set(re.findall(r"--color-([a-z]+)-\d+:", css))
    assert found == set(FAMILIES), f"undocumented families: {found - set(FAMILIES)}"


def test_styleguide_is_not_stale():
    """Regenerating must produce exactly the committed file.

    This is the sync rule as a test: edit a token without regenerating the
    styleguide and this goes red.
    """
    before = STYLEGUIDE.read_text(encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(BUILDER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    after = STYLEGUIDE.read_text(encoding="utf-8")
    assert before == after, (
        "STYLEGUIDE.html is out of date. Run: python scripts/build_styleguide.py"
    )


def test_styleguide_is_standalone():
    """No external requests, and no dependency on Django or a build step."""
    html = STYLEGUIDE.read_text(encoding="utf-8")
    assert "{%" not in html, "contains Django template tags"
    assert "fonts.googleapis.com" not in html
    assert "cdn." not in html
    for remote in ("http://", "https://"):
        assert f'src="{remote}' not in html
        assert f'href="{remote}' not in html


def test_styleguide_has_print_css():
    """A PDF is produced on demand, so print styling is not optional."""
    html = STYLEGUIDE.read_text(encoding="utf-8")
    assert "@media print" in html
    # Printing the dark theme would waste an ink cartridge; it must invert.
    assert "background: #fff !important" in html


def test_pdf_is_not_committed():
    """Deliberate convention: the PDF is generated, never versioned."""
    assert not list(ROOT.glob("*.pdf"))


def test_font_is_self_hosted():
    """DESIGN.md claims a self-hosted face. Claims get checked."""
    files = sorted(FONT_DIR.glob("*.woff2"))
    assert files, "no woff2 files in static/fonts"
    css = INPUT_CSS.read_text(encoding="utf-8")
    assert "@font-face" in css
    assert "fonts.googleapis.com" not in css, "must not fetch from Google at runtime"
    for path in files:
        assert path.name in css, f"{path.name} is present but never referenced"


def test_variable_font_declares_a_weight_range():
    """One variable file covers 400-700; declaring single weights would
    download the identical file twice."""
    css = INPUT_CSS.read_text(encoding="utf-8")
    assert "font-weight: 400 700" in css


def test_tabular_numerals_are_configured():
    """A report is a wall of percentages; proportional digits ruin columns."""
    assert "tabular-nums" in INPUT_CSS.read_text(encoding="utf-8")
