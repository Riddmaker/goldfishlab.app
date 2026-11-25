"""A look as CSS custom properties (B-10), for a style attribute.

Every value is ours: a preset from `looks.presets`, the address of the owner's
image (a UUID and a fixed name), or a colour stored as `#rrggbbaa`. Nothing a
person typed reaches the page unchecked; `looks.colours` saw to the colours.
"""

from django.urls import reverse

from looks import presets
from looks.colours import STORED


def _image(look, kind) -> str:
    return f"url({reverse('looks:image', args=[look.pk, kind])}) center / cover no-repeat"


def mat(look) -> str:
    if look.mat_preset == presets.OWN and look.mat_image:
        return _image(look, "mat")
    return presets.MATS.get(look.mat_preset, presets.MATS[presets.STANDARD_MAT])[1]


def sleeve(look) -> str:
    if look.sleeve_preset == presets.OWN and look.sleeve_image:
        return _image(look, "sleeve")
    return presets.SLEEVES.get(look.sleeve_preset, presets.SLEEVES[presets.STANDARD_SLEEVE])[1]


def _colour(on, value) -> str:
    return value if on and STORED.fullmatch(value or "") else "transparent"


def style(look) -> str:
    """`--mat`, `--sleeve`, `--sleeve-inner`, `--mat-seam`; blank for no look."""
    if look is None:
        return ""
    return (f"--mat: {mat(look)}; --sleeve: {sleeve(look)};"
            f" --sleeve-inner: {_colour(look.inner_sleeve, look.inner_colour)};"
            f" --mat-seam: {_colour(look.seam, look.seam_colour)};")
