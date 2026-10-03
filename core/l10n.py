"""Numbers in a sentence built in Python, in the page's language (phase 12 B).

Templates localize numbers on their own; an f-string never does - `f"{v:.1f}"`
writes "12.5" on a German page, where "12,5" is right. These go through
Django's formats for the active language, so the English look is unchanged.

Only for text a person reads. A number a machine reads (SVG coordinates, a
width in a style, a value in JSON for Mistral) stays an f-string or a plain
number: "12,5" in a coordinate breaks the chart.
"""

from django.utils.formats import number_format
from django.utils.translation import gettext


def number(value, decimals: int | None = None) -> str:
    """`value` with the language's decimal mark and digit grouping: 10,000.5 / 10.000,5.

    Rounded first, as an f-string would: Django's `number_format` cuts the
    digits off instead (12.96 to one place is "12.9" there).
    """
    if decimals is not None:
        value = round(value, decimals)
    return number_format(value, decimals, force_grouping=True)


def percent(value, decimals: int = 0) -> str:
    """"42%" in English; the language decides where the sign goes ("42 %")."""
    return gettext("%(number)s%%") % {"number": number(value, decimals)}


__all__ = ["number", "percent"]
