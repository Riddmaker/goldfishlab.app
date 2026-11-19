"""The news mail (PR 22): each new week once, to whoever asked for it.

A week goes live when its file is deployed, which is not a fixed day, so
`digest` runs every morning (`CELERY_BEAT_SCHEDULE`, beat on worker-short) and
looks at the newest week that is out. A week older than `FRESH` is not sent
any more: news a fortnight late is no news. The rest is the changelog's way
(`changelog.tasks`): batches of `BATCH`, one SMTP connection each, and
`User.news_mailed` written after each mail, so a batch that runs twice sends
nothing the second time.
"""

import logging
import smtplib
from datetime import date, timedelta

from allauth.account.models import EmailAddress
from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone, translation

from core import mailing
from news import issues
from news.services import LIST

logger = logging.getLogger(__name__)

BATCH = 50
FRESH = timedelta(days=7)


def recipients(published: date):
    """Who gets the week out on `published` and has not got it yet."""
    confirmed = EmailAddress.objects.filter(verified=True).values("user_id")
    return (get_user_model().objects
            .filter(news_mail=True, is_active=True, is_guest=False, is_system=False,
                    pk__in=confirmed)
            .filter(Q(news_mailed__isnull=True) | Q(news_mailed__lt=published))
            .order_by("pk"))


def week_to_send(today: date) -> issues.Issue | None:
    issue = issues.latest(today, "en")
    if issue is None or issue.published < today - FRESH:
        return None
    return issue


def message(user, week: str) -> EmailMultiAlternatives:
    """The week's mail for `user`, in their language."""
    request, site = mailing.site()
    unsubscribe_url = mailing.base_url() + LIST.unsubscribe_path(user)
    language = mailing.language_of(user)
    with translation.override(language):
        issue = next(issue for issue in issues.published(language=language)
                     if issue.week == week)
        context = {
            "request": request,
            "current_site": site,
            "issue": issue,
            "unsubscribe_url": unsubscribe_url,
        }
        subject = render_to_string("news/email/digest_subject.txt", context).strip()
        text = render_to_string("news/email/digest_message.txt", context)
        html = render_to_string("news/email/digest_message.html", context)
    mail = EmailMultiAlternatives(subject, text, mailing.sender(), [user.email],
                                  headers=mailing.unsubscribe_headers(unsubscribe_url))
    mail.attach_alternative(html, "text/html")
    return mail


@shared_task(name="news.digest")
def digest(today: str | None = None) -> int:
    """Queue the newest week's mail in batches. The number of accounts queued."""
    if not mailing.mail_on():
        return 0
    issue = week_to_send(date.fromisoformat(today) if today else timezone.localdate())
    if issue is None:
        return 0
    pks = list(recipients(issue.published).values_list("pk", flat=True))
    for start in range(0, len(pks), BATCH):
        send.delay(pks[start:start + BATCH], issue.week, issue.published.isoformat())
    return len(pks)


@shared_task(name="news.send")
def send(pks: list, week: str, published: str) -> int:
    """Send `week`'s mail to these accounts, skipping who already has it.
    The number sent."""
    published = date.fromisoformat(published)
    sent = 0
    with mailing.connection() as smtp:
        for user in recipients(published).filter(pk__in=pks):
            mail = message(user, week)
            mail.connection = smtp
            try:
                mail.send()
            except smtplib.SMTPException:
                # One refused address must not cost everybody after it their
                # mail; it is tried again tomorrow, while the week is fresh.
                logger.warning("news: mail to account %s failed", user.pk, exc_info=True)
                continue
            user.news_mailed = published
            user.save(update_fields=["news_mailed"])
            sent += 1
    logger.info("news: %s mails for %s", sent, week)
    return sent
