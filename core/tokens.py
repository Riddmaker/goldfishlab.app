"""Read design tokens out of assets/css/input.css.

The styleguide renders from the same file the CSS is built from, so a swatch
can never drift from the token it claims to show.

tests/test_design_tokens.py deliberately parses the file with its own copy of
this logic rather than importing from here: a guard that shares code with the
thing it guards is only half a guard.
"""

import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

TOKEN_RE = re.compile(r"--color-([a-z]+)-(\d+):\s*(#[0-9a-fA-F]{6});")

FAMILY_NOTES = {
    "ink": "Warm near-black. The page ground and all body text.",
    "parchment": "Aged paper. Surfaces, and light text on the dark ground.",
    "blood": "Desaturated crimson. Accent and danger. Never body text.",
    "verdigris": "Aged copper. Success, and 'modelled' states.",
}

FAMILY_ORDER = ["ink", "parchment", "blood", "verdigris"]


@lru_cache(maxsize=1)
def _read_css() -> str:
    path = Path(settings.BASE_DIR) / "assets" / "css" / "input.css"
    return path.read_text(encoding="utf-8")


def color_families(*, use_cache: bool = True) -> list[dict]:
    """Every colour family with its steps, ordered for display."""
    if not use_cache:
        _read_css.cache_clear()

    found: dict[str, list[dict]] = {}
    for family, step, value in TOKEN_RE.findall(_read_css()):
        found.setdefault(family, []).append({"step": int(step), "value": value.lower()})

    families = []
    for name in FAMILY_ORDER:
        if name not in found:
            continue
        families.append(
            {
                "name": name,
                "note": FAMILY_NOTES.get(name, ""),
                "steps": sorted(found[name], key=lambda s: s["step"]),
            }
        )
    return families
