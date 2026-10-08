"""Who gets the changelog by mail, and how they stop (C7).

The mail is off for everybody until they ask for it, and it is only asked for
by a click on "Subscribe by email" (/changelog/) or the switch on "Your plan".

**Without an account the click is remembered, not acted on.** The visitor is
sent to sign up (or, already a guest, to save their deck) with the wish kept
in the session for a day (`want`). The account then subscribes when it is
made (`user_signed_up`), which still works when the confirmation link is
opened in another browser, and once more when it signs in (`user_logged_in`),
which is when an existing account says yes and the page says so
(`changelog.signals`). A day, so a click nobody followed up does not subscribe
whoever signs in on that browser next week.

**Stopping takes one click wherever the mail is.** Every mail carries a signed
link (`unsubscribe_token`) that only ever switches the mail off, never on, and
never expires - a mail from a year ago must still work.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import reverse
from django.utils import timezone

#: The session key of a wish to subscribe made before there was an account.
INTENT = "changelog_mail_wanted"
INTENT_FOR = timedelta(days=1)
SALT = "changelog.unsubscribe"


def mail_on() -> bool:
    """Is there a sender? Without one the page offers the feed alone, so
    nobody subscribes to a mail that would never come."""
    return bool(settings.CHANGELOG_FROM_EMAIL)


def language_of(user) -> str:
    """The language of this account's mail: its own choice while that is
    switched on, else English (as `accounts.middleware` does for pages)."""
    if user.language in dict(settings.LANGUAGES):
        return user.language
    return settings.LANGUAGE_CODE


def can_subscribe(user) -> bool:
    """An account that can sign in and has an address of its own."""
    return (user.is_authenticated and user.is_active
            and not user.is_guest and not user.is_system)


def subscribe(user) -> bool:
    """Switch the mail on. True if it was off."""
    if user.changelog_mail:
        return False
    user.changelog_mail = True
    user.changelog_mail_since = timezone.now()
    user.save(update_fields=["changelog_mail", "changelog_mail_since"])
    return True


def unsubscribe(user) -> bool:
    """Switch the mail off, and forget when it was switched on. True if it
    was on."""
    if not user.changelog_mail:
        return False
    user.changelog_mail = False
    user.changelog_mail_since = None
    user.save(update_fields=["changelog_mail", "changelog_mail_since"])
    return True


# --- the wish, before there is an account ---------------------------------


def want(request) -> None:
    request.session[INTENT] = timezone.now().timestamp()


def wanted(request) -> bool:
    """Is there a wish from the last day?"""
    stamp = request.session.get(INTENT)
    if not isinstance(stamp, int | float):
        return False
    return timezone.now().timestamp() - stamp <= INTENT_FOR.total_seconds()


def take(request) -> bool:
    """`wanted`, and forget it."""
    found = wanted(request)
    request.session.pop(INTENT, None)
    return found


def keep_across(request, sign_out) -> None:
    """Call `sign_out(request)`, which empties the session, and keep the wish.
    A guest is signed out before it becomes an account (`guests.views`) or
    signs in as one (`guests.middleware`)."""
    stamp = request.session.get(INTENT)
    sign_out(request)
    if stamp is not None:
        request.session[INTENT] = stamp


# --- the link in every mail -----------------------------------------------


def unsubscribe_token(user) -> str:
    return signing.dumps(user.pk, salt=SALT)


def user_for(token: str):
    """The account a token was made for, or None."""
    try:
        pk = signing.loads(token, salt=SALT)
    except signing.BadSignature:
        return None
    return get_user_model().objects.filter(pk=pk).first()


def unsubscribe_path(user) -> str:
    return reverse("changelog_unsubscribe", args=[unsubscribe_token(user)])
