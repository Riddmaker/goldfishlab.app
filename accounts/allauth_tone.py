"""allauth's own strings whose translation takes the wrong tone (phase 12, Q8).

allauth's German says "Sie" in a few places; this site says "du". And it names
the sign-in page and its button "Anmeldung" (a noun) where the rest of the site
says "Anmelden". Our catalogue comes first in `LOCALE_PATHS`, so an entry there
replaces allauth's. But `makemessages` only keeps a msgid it finds in our
source, so the strings are named here - never called, only read by
`makemessages`.

Each language overrides only its own list. In every other catalogue the entry
stays empty: an empty entry is not compiled, and allauth's own text shows.
The lists came from the allauth pages and messages a visitor can meet
(allauth 65.19.4); after an allauth upgrade, check them again. Japanese needs
none: allauth's Japanese is already polite.
"""

from itertools import chain

from django.utils.translation import gettext_noop

OVERRIDES = {
    # "Sie" -> "du", and "Anmeldung" -> "Anmelden".
    "de": (
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
    ),
    # "Connexion" -> "Se connecter"; "clé secrète" -> "clé de sécurité".
    "fr": (
        gettext_noop("Sign In"),
        gettext_noop("Use a security key"),
        gettext_noop("Use authenticator app or code"),
        gettext_noop("Use your password"),
    ),
    # "Lei" -> "tu".
    "it": (
        gettext_noop("Please reauthenticate to safeguard your account."),
    ),
    # "usted" -> "tú".
    "es": (
        gettext_noop("Already have an account? Then please %(link)ssign in%(end_link)s."),
        gettext_noop("Are you sure you want to sign out?"),
        gettext_noop("Do you really want to remove the selected email address?"),
        gettext_noop("Enter Sign-In Code"),
        gettext_noop("Enter your password:"),
        gettext_noop("Forgot your password?"),
        gettext_noop("Forgotten your password? Enter your email address below, and we'll send "
                     "you an email allowing you to reset it."),
        gettext_noop("If you have not created an account yet, then please "
                     "%(link)ssign up%(end_link)s first."),
        gettext_noop("Please contact us if you have any trouble resetting your password."),
        gettext_noop("Please reauthenticate to safeguard your account."),
        gettext_noop("Please type your current password."),
        gettext_noop("Request Code"),
        gettext_noop("Successfully signed in as %(name)s."),
        gettext_noop("The email address and/or password you specified are not correct."),
        gettext_noop("The following email addresses are associated with your account:"),
        gettext_noop('The password reset link was invalid, possibly because it has already been '
                     'used.  Please request a <a href="%(passwd_reset_url)s">new password '
                     'reset</a>.'),
        gettext_noop("The phone number and/or password you specified are not correct."),
        gettext_noop("The username and/or password you specified are not correct."),
        gettext_noop("Too many failed login attempts. Try again later."),
        gettext_noop("Use your password"),
        gettext_noop("Username can not be used. Please use other username."),
        gettext_noop("Verify Your Email Address"),
        gettext_noop("We have sent you an email. If you have not received it please check your "
                     "spam folder. Otherwise contact us if you do not receive it in a few "
                     "minutes."),
        gettext_noop("You are already logged in as %(user_display)s."),
        gettext_noop("You cannot add more than %d email addresses."),
        gettext_noop("You cannot remove your primary email address (%(email)s)."),
        gettext_noop("You cannot remove your primary email address."),
        gettext_noop("You currently do not have any email address set up. You should really add "
                     "an email address so you can receive notifications, reset your password, "
                     "etc."),
        gettext_noop("You have signed out."),
        gettext_noop("You must type the same password each time."),
        gettext_noop("You will receive a special code for a password-free sign-in."),
        gettext_noop("Your email address is still pending verification."),
        gettext_noop("Your password is now changed."),
        gettext_noop("Your phone number is still pending verification."),
        gettext_noop("Your primary email address must be verified."),
    ),
    # allauth leaves "sign in" in English inside the sentence.
    "pt-br": (
        gettext_noop("Already have an account? Then please %(link)ssign in%(end_link)s."),
    ),
}

#: Every msgid some language overrides.
OVERRIDDEN = frozenset(chain.from_iterable(OVERRIDES.values()))
