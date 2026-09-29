"""The operator is named, and the retention periods the policy states are enforced.

The legal pages make two kinds of claim a test can hold them to: that somebody
is named as responsible (production refuses to boot otherwise, core.E003), and
that data lives only as long as the privacy policy says (core.tasks). The
wording itself is covered by tests/test_privacy.py.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone

from billing.models import StripeEvent
from core import tasks
from core.checks import check_operator_is_named
from decks.models import PendingImport

pytestmark = pytest.mark.django_db

User = get_user_model()


# --- core.E003 ---------------------------------------------------------------


def _name_the_operator(settings):
    settings.LEGAL_OPERATOR_NAME = "Erika Muster"
    settings.LEGAL_OPERATOR_ADDRESS = ["Musterstrasse 1", "8000 Zürich"]
    settings.LEGAL_CONTACT_EMAIL = "hello@example.ch"


@pytest.mark.parametrize(
    "blank", ["LEGAL_OPERATOR_NAME", "LEGAL_OPERATOR_ADDRESS", "LEGAL_CONTACT_EMAIL"]
)
def test_production_refuses_to_boot_with_any_part_missing(settings, blank):
    """A privacy policy that names no controller is not a privacy policy."""
    settings.LEGAL_DETAILS_REQUIRED = True
    _name_the_operator(settings)
    setattr(settings, blank, [] if blank == "LEGAL_OPERATOR_ADDRESS" else "")

    errors = check_operator_is_named(None)

    assert [error.id for error in errors] == ["core.E003"]
    assert blank in errors[0].msg


def test_a_named_operator_passes(settings):
    settings.LEGAL_DETAILS_REQUIRED = True
    _name_the_operator(settings)
    assert check_operator_is_named(None) == []


def test_development_does_not_need_one(settings):
    settings.LEGAL_DETAILS_REQUIRED = False
    settings.LEGAL_OPERATOR_NAME = ""
    assert check_operator_is_named(None) == []


def test_production_settings_require_it():
    """The switch lives in prod.py; this is what makes E003 fire there at all.

    Read as text rather than imported: importing prod.py reads the production
    environment, which a test run does not have.
    """
    from django.conf import settings

    source = (settings.BASE_DIR / "goldfishlab" / "settings" / "prod.py").read_text("utf-8")
    assert "\nLEGAL_DETAILS_REQUIRED = True\n" in source


# --- housekeeping ------------------------------------------------------------


def _age(queryset, field, days):
    queryset.update(**{field: timezone.now() - timedelta(days=days)})


def test_an_abandoned_upload_is_deleted_after_a_week():
    owner = User.objects.create_user(email="a@example.com", password="pw-test-only")
    old = PendingImport.objects.create(owner=owner, kind="deck", text="1 Sol Ring")
    other = User.objects.create_user(email="b@example.com", password="pw-test-only")
    fresh = PendingImport.objects.create(owner=other, kind="deck", text="1 Swamp")
    _age(PendingImport.objects.filter(pk=old.pk), "created_at", tasks.PENDING_IMPORT_DAYS + 1)

    counts = tasks.run()

    assert counts["pending_imports_deleted"] == 1
    assert list(PendingImport.objects.values_list("pk", flat=True)) == [fresh.pk]


def test_an_old_stripe_payload_is_emptied_but_its_id_kept():
    """The id is what makes a late retry idempotent; the email in it is not needed."""
    old = StripeEvent.objects.create(
        stripe_event_id="evt_old", type="checkout.session.completed",
        payload={"data": {"object": {"customer_details": {"email": "a@example.com"}}}},
    )
    fresh = StripeEvent.objects.create(
        stripe_event_id="evt_fresh", type="invoice.paid", payload={"id": "evt_fresh"},
    )
    _age(StripeEvent.objects.filter(pk=old.pk), "processed_at", tasks.STRIPE_PAYLOAD_DAYS + 1)

    counts = tasks.run()

    old.refresh_from_db()
    fresh.refresh_from_db()
    assert counts["stripe_payloads_emptied"] == 1
    assert old.payload == {} and old.stripe_event_id == "evt_old"
    assert fresh.payload == {"id": "evt_fresh"}


def test_expired_sessions_are_cleared(client):
    owner = User.objects.create_user(email="b@example.com", password="pw-test-only")
    client.force_login(owner)
    Session.objects.update(expire_date=timezone.now() - timedelta(seconds=1))

    tasks.run()

    assert not Session.objects.exists()


def test_the_task_is_scheduled_daily(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["housekeeping"]
    assert entry["task"] == tasks.housekeeping.name
