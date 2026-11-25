"""Colours a person types in for their look (B-10).

A colour ends up inside a CSS custom property, so it is never stored as typed:
it is read as hex (#rgb, #rgba, #rrggbb, #rrggbbaa) or rgb()/rgba() and kept
as `#rrggbbaa`, the one form the page writes out. Anything else is refused.
"""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

HEX = re.compile(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})")
RGB = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(\d*\.?\d+)(%?)\s*)?\)"
)
STORED = re.compile(r"#[0-9a-f]{8}")


def normalise(value: str) -> str:
    """`value` as `#rrggbbaa`, or ValidationError."""
    text = (value or "").strip().lower()
    if match := HEX.fullmatch(text):
        digits = match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits)
        return "#" + digits.ljust(8, "f")
    if match := RGB.fullmatch(text):
        channels = [int(match.group(i)) for i in (1, 2, 3)]
        alpha = 1.0 if match.group(4) is None else float(match.group(4))
        if match.group(5):
            alpha /= 100
        if all(0 <= c <= 255 for c in channels) and 0 <= alpha <= 1:
            return "#" + "".join(f"{c:02x}" for c in channels) + f"{round(alpha * 255):02x}"
    raise ValidationError(
        _("Enter a colour as #rrggbb or rgba(r, g, b, a)."), code="colour"
    )


def validate_stored(value: str) -> None:
    """A model field holds `#rrggbbaa` or nothing."""
    if value and not STORED.fullmatch(value):
        raise ValidationError(_("Enter a colour as #rrggbb or rgba(r, g, b, a)."), code="colour")
