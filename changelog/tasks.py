"""The changelog's monthly mail (C7).

On the first of each month (`CELERY_BEAT_SCHEDULE`, beat on worker-short),
`digest` looks at the month before. No entry, no mail. Otherwise every account
that asked for it and whose address is confirmed gets one mail in its own
language, sent in batches of `BATCH`: one SMTP connection each, and well
inside the time limit a task has (`CELERY_TASK_SOFT_TIME_LIMIT`).

`User.changelog_mailed` is written after each mail, so a batch that runs
twice - a worker killed mid-batch, its message delivered again - skips who
already has the month's mail.
"""

import logging
import smtplib
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from allauth.account.models import EmailAddress
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone, translation

from changelog import entries, services

logger = logging.getLogger(__name__)

BATCH = 50


def last_month(today: date | None = None) -> date:
    """The first day of the month before `today`'s."""
    today = today or timezone.localdate()
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def recipients(month: date):
    """Who gets `month`'s mail and has not got it yet."""
    confirmed = EmailAddress.objects.filter(verified=True).values("user_id")
    return (get_user_model().objects
            .filter(changelog_mail=True, is_active=True, is_guest=False, is_system=False,
                    pk__in=confirmed)
            .filter(Q(changelog_mailed__isnull=True) | Q(changelog_mailed__lt=month))
            .order_by("pk"))


def connection():
    """The changelog's own SMTP login when it has one, else the site's."""
    if settings.CHANGELOG_EMAIL_HOST_USER:
        return get_connection(username=settings.CHANGELOG_EMAIL_HOST_USER,
                              password=settings.CHANGELOG_EMAIL_HOST_PASSWORD)
    return get_connection()


def _site():
    """What the mail templates read from a request (`account/email/base_message.*`),
    from `SITE_URL`: a task has no request."""
    parts = urlsplit(settings.SITE_URL or "https://goldfishlab.app")
    return SimpleNamespace(scheme=parts.scheme), SimpleNamespace(domain=parts.netloc)


def message(user, month: date) -> EmailMultiAlternatives:
    """`month`'s mail for `user`, in their language."""
    request, site = _site()
    base = f"{request.scheme}://{site.domain}"
    unsubscribe_url = base + services.unsubscribe_path(user)
    with translation.override(services.language_of(user)):
        context = {
            "request": request,
            "current_site": site,
            "month": month,
            "entries": entries.of_month(month),
            "unsubscribe_url": unsubscribe_url,
            "since": user.changelog_mail_since,
        }
        subject = render_to_string("changelog/email/digest_subject.txt", context).strip()
        text = render_to_string("changelog/email/digest_message.txt", context)
        html = render_to_string("changelog/email/digest_message.html", context)
    mail = EmailMultiAlternatives(
        subject, text, settings.CHANGELOG_FROM_EMAIL, [user.email],
        headers={
            # One click in the mail program's own "Unsubscribe" (RFC 8058).
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    mail.attach_alternative(html, "text/html")
    return mail


@shared_task(name="changelog.digest")
def digest(today: str | None = None) -> int:
    """Queue last month's mail in batches. The number of accounts queued."""
    if not services.mail_on():
        return 0
    month = last_month(date.fromisoformat(today) if today else None)
    if not entries.of_month(month):
        return 0
    pks = list(recipients(month).values_list("pk", flat=True))
    for start in range(0, len(pks), BATCH):
        send.delay(pks[start:start + BATCH], month.isoformat())
    return len(pks)


@shared_task(name="changelog.send")
def send(pks: list, month: str) -> int:
    """Send `month`'s mail to these accounts, skipping who already has it.
    The number sent."""
    month = date.fromisoformat(month)
    sent = 0
    with connection() as smtp:
        for user in recipients(month).filter(pk__in=pks):
            mail = message(user, month)
            mail.connection = smtp
            try:
                mail.send()
            except smtplib.SMTPException:
                # One refused address must not cost everybody after it
                # their mail; it is tried again next month.
                logger.warning("changelog: mail to account %s failed", user.pk, exc_info=True)
                continue
            user.changelog_mailed = month
            user.save(update_fields=["changelog_mailed"])
            sent += 1
    logger.info("changelog: %s mails for %s", sent, month)
    return sent
