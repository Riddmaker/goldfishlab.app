"""Asking an account to read a changed privacy policy once (D2).

`CONSENT_VERSION` is the date of the last change a person must see before
going on - not every edit: a typo fixed or a counter renamed moves the
policy's "Last updated" date (`core.views.PrivacyView`) and asks nobody. A
change in what we may do with a page (the first one: a labelled sponsor and
labelled shop links, 2026-10) moves this date too.

An account whose `privacy_accepted` is older is sent, on its next page, to a
page that shows what changed and asks for an OK. Only a plain GET is sent
there: a form being posted, or a page part the browser fetches in the
background (htmx), goes on as asked. The legal pages, the account's own data
and deletion, signing out and the admin stay open, so nobody has to agree in
order to leave. A guest has agreed to nothing yet and the site's own lab
account cannot sign in; both are left alone. A new account agrees when it
is made with the current version (`current`): the sign-up page links both
documents and says that creating the account accepts them.
"""

from datetime import date

from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils.http import urlencode

CONSENT_VERSION = date(2026, 10, 8)


def current() -> date:
    """The default of `User.privacy_accepted`: a new account agrees to the
    version it signed up under."""
    return CONSENT_VERSION

#: Pages that open without the OK: the documents themselves, the account's
#: data and deletion (`accounts:`), signing out, the admin, the language
#: switcher.
OPEN_NAMESPACES = frozenset({"accounts", "admin"})
OPEN_NAMES = frozenset({"privacy", "terms", "imprint", "account_logout", "set_language"})


def needs_consent(user) -> bool:
    if not user.is_authenticated or user.is_guest or user.is_system:
        return False
    return user.privacy_accepted is None or user.privacy_accepted < CONSENT_VERSION


def accept(user) -> None:
    user.privacy_accepted = CONSENT_VERSION
    user.save(update_fields=["privacy_accepted"])


def _is_open(path: str) -> bool:
    try:
        match = resolve(path)
    except Resolver404:
        return True  # a 404 is a 404 for everybody
    return bool(OPEN_NAMESPACES & set(match.namespaces)) or match.url_name in OPEN_NAMES


class ConsentMiddleware:
    """After authentication and the guest fence: it reads `request.user`."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.method == "GET"
                and "HX-Request" not in request.headers
                and needs_consent(request.user)
                and not _is_open(request.path_info)):
            target = reverse("accounts:privacy_update")
            return redirect(f"{target}?{urlencode({'next': request.get_full_path()})}")
        return self.get_response(request)
