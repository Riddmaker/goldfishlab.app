"""allauth's own strings whose translation takes the wrong tone (phase 12, Q8).

allauth's German says "Sie" in a few places; this site says "du". And it names
the sign-in page and its button "Anmeldung" (a noun) where the rest of the site
says "Anmelden". Our catalogue comes first in `LOCALE_PATHS`, so an entry there
replaces allauth's. But `makemessages` only keeps a msgid it finds in our
source, so the strings are named here - never called, only read by
`makemessages`.

A language whose allauth catalogue already has the right tone leaves these
untranslated: an empty entry is not compiled, and allauth's own text shows.
The list came from the allauth pages and messages a visitor can meet
(allauth 65.19.4); after an allauth upgrade, check it again.
"""

from django.utils.translation import gettext_noop

OVERRIDDEN = (
    gettext_noop("Enter Email Verification Code"),
    gettext_noop("Enter Phone Verification Code"),
    gettext_noop("Enter a phone number including country code (e.g. +1 for the US)."),
    gettext_noop("Enter your password:"),
    gettext_noop("Sign In"),
    gettext_noop("Please reauthenticate to safeguard your account."),
    gettext_noop("Use a security key"),
    gettext_noop("Use authenticator app or code"),
    gettext_noop("Use your password"),
    gettext_noop("We've sent a code to %(recipient)s. The code expires shortly, so please "
                 "enter it soon."),
    gettext_noop("You have verified phone number %(phone)s."),
    gettext_noop("You will receive a special code for a password-free sign-in."),
)
