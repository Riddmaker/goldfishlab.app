"""Housekeeping: the retention periods the privacy policy states, enforced.

A privacy policy that says "kept for 30 days" is a claim about a schedule. This
is the schedule. Before it existed three kinds of row lived for ever: expired
sessions (nothing ran `clearsessions`), uploads abandoned on the column-mapping
screen, and the full payload of every Stripe webhook event - which carries the
customer's email and name, and is not tied to a user, so deleting an account
did not remove it.
"""

import logging
from datetime import timedelta
from importlib import import_module

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

#: An upload nobody finished mapping. Long enough for a person who closed the
#: tab to come back the next day; short enough that a forgotten file does not
#: sit in the database indefinitely.
PENDING_IMPORT_DAYS = 7

#: Stripe retries a failed delivery for up to three days, and the payload is
#: only ever read while diagnosing one. After that the row is kept, emptied,
#: because its event id is what makes a late retry idempotent.
STRIPE_PAYLOAD_DAYS = 30


def run() -> dict:
    """Do the work; returned counts are for the log and the tests."""
    from billing.models import StripeEvent
    from decks.models import PendingImport

    now = timezone.now()
    engine = import_module(settings.SESSION_ENGINE)
    engine.SessionStore.clear_expired()

    pending, _ = PendingImport.objects.filter(
        created_at__lt=now - timedelta(days=PENDING_IMPORT_DAYS)
    ).delete()
    emptied = (
        StripeEvent.objects.filter(processed_at__lt=now - timedelta(days=STRIPE_PAYLOAD_DAYS))
        .exclude(payload={})
        .update(payload={})
    )
    return {"pending_imports_deleted": pending, "stripe_payloads_emptied": emptied}


@shared_task(name="core.housekeeping")
def housekeeping() -> dict:
    counts = run()
    logger.info("housekeeping %s", counts)
    return counts
