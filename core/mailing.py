"""The mails a person asks for: the changelog (C7) and the weekly news (PR 22).

Both work the same way, so the rules live here once:

* **Off until asked for.** A list is switched on only by a click - on its own
  page or on "Your plan" - and the moment it was is kept beside the switch
  (`<name>_mail_since`), as the consent we must be able to show.
* **A wish made without an account is remembered, not acted on.** The click
  is kept in the session for a day (`MailList.want`); the account subscribes
  when it is made, or when it signs in (each app's `signals`).
* **Stopping takes one click wherever the mail is.** Every mail carries a
  signed link that only ever switches its own list off and never expires.
  Each list has its own salt, so a changelog link cannot stop the news.

Both lists go out from the changelog's sender and its SMTP login: one mailbox
for the mail that goes to many (`CHANGELOG_FROM_EMAIL`).
"""

from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import get_connection
from django.urls import reverse
from django.utils import timezone

#: How long a wish made before there was an account is kept.
INTENT_FOR = timedelta(days=1)
#: Every list's wish is a session key ending in this.
INTENT_SUFFIX = "_mail_wanted"


def mail_on() -> bool:
    """Is there a sender? Without one the pages offer their feeds alone, so
    nobody subscribes to a mail that would never come."""
    return bool(settings.CHANGELOG_FROM_EMAIL)


def sender() -> str:
    return settings.CHANGELOG_FROM_EMAIL


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


def keep_across(request, sign_out) -> None:
    """Call `sign_out(request)`, which empties the session, and keep every
    list's wish. A guest is signed out before it becomes an account
    (`guests.views`) or signs in as one (`guests.middleware`)."""
    kept = {key: value for key, value in request.session.items()
            if key.endswith(INTENT_SUFFIX)}
    sign_out(request)
    for key, value in kept.items():
        request.session[key] = value


def connection():
    """The changelog's own SMTP login when it has one, else the site's."""
    if settings.CHANGELOG_EMAIL_HOST_USER:
        return get_connection(username=settings.CHANGELOG_EMAIL_HOST_USER,
                              password=settings.CHANGELOG_EMAIL_HOST_PASSWORD)
    return get_connection()


def site():
    """What the mail templates read from a request (`account/email/base_message.*`),
    from `SITE_URL`: a task has no request."""
    parts = urlsplit(settings.SITE_URL or "https://goldfishlab.app")
    return SimpleNamespace(scheme=parts.scheme), SimpleNamespace(domain=parts.netloc)


def base_url() -> str:
    request, current = site()
    return f"{request.scheme}://{current.domain}"


def unsubscribe_headers(url: str) -> dict:
    """One click in the mail program's own "Unsubscribe" (RFC 8058)."""
    return {"List-Unsubscribe": f"<{url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


@dataclass(frozen=True)
class MailList:
    """One mail a person can ask for. `name` is the prefix of its fields on
    the account (`<name>_mail`, `<name>_mail_since`) and of its wish in the
    session; `salt` signs its unsubscribe link and must never change, or every
    mail already sent loses its way out."""

    name: str
    salt: str
    #: The view of the link in every mail.
    unsubscribe_view: str

    @property
    def field(self) -> str:
        return f"{self.name}_mail"

    @property
    def since_field(self) -> str:
        return f"{self.name}_mail_since"

    @property
    def intent(self) -> str:
        return f"{self.name}{INTENT_SUFFIX}"

    def is_on(self, user) -> bool:
        return can_subscribe(user) and getattr(user, self.field)

    def subscribe(self, user) -> bool:
        """Switch the mail on. True if it was off."""
        if getattr(user, self.field):
            return False
        setattr(user, self.field, True)
        setattr(user, self.since_field, timezone.now())
        user.save(update_fields=[self.field, self.since_field])
        return True

    def unsubscribe(self, user) -> bool:
        """Switch the mail off, and forget when it was switched on. True if
        it was on."""
        if not getattr(user, self.field):
            return False
        setattr(user, self.field, False)
        setattr(user, self.since_field, None)
        user.save(update_fields=[self.field, self.since_field])
        return True

    # --- the wish, before there is an account -----------------------------

    def want(self, request) -> None:
        request.session[self.intent] = timezone.now().timestamp()

    def wanted(self, request) -> bool:
        """Is there a wish from the last day?"""
        stamp = request.session.get(self.intent)
        if not isinstance(stamp, int | float):
            return False
        return timezone.now().timestamp() - stamp <= INTENT_FOR.total_seconds()

    def take(self, request) -> bool:
        """`wanted`, and forget it."""
        found = self.wanted(request)
        request.session.pop(self.intent, None)
        return found

    # --- the link in every mail -------------------------------------------

    def token(self, user) -> str:
        """The same for an account every time: a plain `Signer`, not
        `signing.dumps`, whose timestamp would make every mail's link differ
        and is no use to a link that never expires."""
        return signing.Signer(salt=self.salt).sign(str(user.pk))

    def user_for(self, token: str):
        """The account a token was made for, or None."""
        try:
            pk = signing.Signer(salt=self.salt).unsign(token)
        except signing.BadSignature:
            return None
        return get_user_model().objects.filter(pk=pk).first()

    def unsubscribe_path(self, user) -> str:
        return reverse(self.unsubscribe_view, args=[self.token(user)])
