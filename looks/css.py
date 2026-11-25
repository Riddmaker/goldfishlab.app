"""A look as CSS custom properties (B-10), for a style attribute.

Every value is ours: a preset from `looks.presets`, the address of the owner's
image (a UUID and a fixed name), or a colour stored as `#rrggbbaa`. Nothing a
person typed reaches the page unchecked; `looks.colours` saw to the colours.
"""

from django.urls import reverse

from looks import images, presets
from looks.colours import STORED


def _has(look, kind) -> bool:
    """The look's own image for `kind`, if its file is really there: a file
    lost from the share falls back to the template without a word (no backup
    of the share, by decision; the owner can upload it again)."""
    relative = getattr(look, f"{kind}_image")
    if not relative:
        return False
    try:
        return images.resolve(relative).is_file()
    except ValueError:
        return False


def _image(look, kind) -> str:
    return f"url({reverse('looks:image', args=[look.pk, kind])}) center / cover no-repeat"


def mat(look) -> str:
    if look.mat_preset == presets.OWN and _has(look, images.MAT):
        return _image(look, "mat")
    return presets.MATS.get(look.mat_preset, presets.MATS[presets.STANDARD_MAT])[1]


def sleeve(look) -> str:
    if look.sleeve_preset == presets.OWN and _has(look, images.SLEEVE):
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
