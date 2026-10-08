"""Every account mail allauth can send, rendered with a realistic context.

The mails themselves are templates (templates/account/email/). This module is
what lets them be looked at and tested without walking through each flow:
`manage.py preview_mails` writes them to files for a browser, and
tests/test_mails.py renders every one. The contexts mirror what allauth
65.19.4 passes (DefaultAccountAdapter.send_mail, send_confirmation_mail,
send_notification_mail and the flows that call them).
"""

from allauth.account.adapter import get_adapter
from allauth.core import context
from django.contrib.sites.shortcuts import get_current_site
from django.urls import reverse
from django.utils import timezone

EMAIL = "player@mail.test"
KEY = "MQ:1tSample:confirmation-key-for-previews"
CODE = "7KQ2MX"


def _notification(**extra):
    """What send_notification_mail adds to every security notification."""

    def build(request):
        return {
            "timestamp": timezone.now(),
            "ip": "203.0.113.7",
            "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) Safari/605.1",
            **extra,
        }

    return build


def _confirmation(request):
    url = reverse("account_confirm_email", args=[KEY])
    return {"key": KEY, "activate_url": request.build_absolute_uri(url)}


def _password_reset_key(request):
    url = reverse("account_reset_password_from_key", kwargs={"uidb36": "1a", "key": "sample-token"})
    return {"password_reset_url": request.build_absolute_uri(url)}


def _wrong_door(request):
    return {
        "signup_url": request.build_absolute_uri(reverse("account_signup")),
        "password_reset_url": request.build_absolute_uri(reverse("account_reset_password")),
    }


# Template prefix (under account/email/) -> the extra context it is sent with.
SAMPLES = {
    "email_confirmation_signup": _confirmation,
    "email_confirmation": _confirmation,
    "password_reset_key": _password_reset_key,
    "password_reset_code": lambda request: {"code": CODE},
    "login_code": lambda request: {"code": CODE},
    "unknown_account": _wrong_door,
    "account_already_exists": _wrong_door,
    "email_confirm": _notification(),
    "email_changed": _notification(from_email="old-address@mail.test", to_email=EMAIL),
    "email_deleted": _notification(deleted_email="old-address@mail.test"),
    "password_changed": _notification(),
    "password_reset": _notification(),
    "password_set": _notification(),
}


def changelog_digest():
    """The changelog's monthly mail (C7), for October 2026, to EMAIL. Not
    allauth's: `changelog.tasks.message` builds it without a request."""
    from datetime import date

    from accounts.models import User
    from changelog import tasks

    return tasks.message(User(pk=1, email=EMAIL, changelog_mail=True), date(2026, 10, 1))


def render(prefix, request):
    """Render one mail the way allauth's adapter would send it to EMAIL."""
    ctx = {
        "request": request,
        "email": EMAIL,
        "current_site": get_current_site(request),
        **SAMPLES[prefix](request),
    }
    with context.request_context(request):
        return get_adapter(request).render_mail(f"account/email/{prefix}", EMAIL, ctx)
