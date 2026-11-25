"""The ready-made mats and sleeves (B-10): CSS only, drawn from our colours.

A preset is a CSS `background` value written here, never anything a person
typed, so it can go into a style attribute as it is. The first of each is the
standard look the site has always had.
"""

from django.utils.translation import gettext_lazy as _

OWN = "own"

MATS = {
    "ink": (_("Ink"), "radial-gradient(ellipse at 50% 40%, #2f2b24, #0f0e0b 75%)"),
    "felt": (_("Felt"), "radial-gradient(ellipse at 50% 40%, #224f3e, #132c23 75%)"),
    "blood": (_("Blood"), "radial-gradient(ellipse at 50% 40%, #6a1c17, #2a0b09 75%)"),
    "water": (_("Water"), "radial-gradient(ellipse at 50% 40%, #2f5673, #142636 75%)"),
    "parchment": (_("Parchment"), "radial-gradient(ellipse at 50% 40%, #7c5e2c, #3d2e18 75%)"),
}

SLEEVES = {
    "parchment": (_("Parchment"),
                  "repeating-linear-gradient(45deg, #5c4522 0 6px, #3d2e18 6px 12px)"),
    "ink": (_("Ink"), "linear-gradient(160deg, #464036, #1c1a15)"),
    "blood": (_("Blood"), "linear-gradient(160deg, #c43d33, #4a1410)"),
    "verdigris": (_("Verdigris"),
                  "repeating-linear-gradient(-45deg, #224f3e 0 5px, #132c23 5px 10px)"),
    "water": (_("Water"), "radial-gradient(circle at 30% 25%, #9cc4dc, #2f5673 70%)"),
    "gold": (_("Gold"),
             "repeating-linear-gradient(45deg, #b5904f 0 4px, #7c5e2c 4px 8px),"
             " linear-gradient(#b5904f, #7c5e2c)"),
}

#: Colour suggestions beside the inner sleeve and the seam.
SWATCHES = ("#dcc79b", "#b5904f", "#c43d33", "#7bb89e", "#9cc4dc", "#f4ecdc", "#1c1a15")

STANDARD_MAT = next(iter(MATS))
STANDARD_SLEEVE = next(iter(SLEEVES))
