"""Django's own month names where a language writes them differently (phase 12 J6).

Django's Italian capitalises the months ("4 Ottobre 2026"); Italian writes
them small ("4 ottobre 2026"). The names come from Django's catalogue
(`django.utils.dates.MONTHS`), so no date format can change them. Our
catalogue comes first in `LOCALE_PATHS`, so an entry there replaces Django's -
the same way as `accounts/allauth_tone.py`. The msgids are named here only so
`makemessages` keeps them; nothing calls this.

In every other catalogue the entries stay empty: an empty entry is not
compiled, and Django's own month names show.
"""

from itertools import chain

from django.utils.translation import gettext_noop

_MONTHS = (
    gettext_noop("January"),
    gettext_noop("February"),
    gettext_noop("March"),
    gettext_noop("April"),
    gettext_noop("May"),
    gettext_noop("June"),
    gettext_noop("July"),
    gettext_noop("August"),
    gettext_noop("September"),
    gettext_noop("October"),
    gettext_noop("November"),
    gettext_noop("December"),
)

OVERRIDES = {
    "it": _MONTHS,
}

#: Every msgid some language overrides.
OVERRIDDEN = frozenset(chain.from_iterable(OVERRIDES.values()))
