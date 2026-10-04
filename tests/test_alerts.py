"""Phase 12 J12: the operator hears about failed runs, a stuck queue and a
failed catalogue job - once a day per kind, and with nothing about a person."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from cards.models import BulkImport
from core import alerts, tasks
from decks.models import Deck
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

Status = SimulationRun.Status


@pytest.fixture(autouse=True)
def recipient(settings):
    settings.ALERT_EMAIL = "hello@example.ch"


@pytest.fixture
def deck():
    owner = get_user_model().objects.create_user(email="private@example.com", password="pw")
    return Deck.objects.create(owner=owner, name="Secret Deck Name")


def make_run(deck, status, **fields):
    run = SimulationRun.objects.create(
        owner=deck.owner, deck=deck, games_total=1000, seed=1, status=status
    )
    SimulationRun.objects.filter(pk=run.pk).update(**fields)
    run.refresh_from_db()
    return run


def test_all_quiet_sends_nothing(mailoutbox):
    assert alerts.check() == []
    assert mailoutbox == []


def test_a_failed_run_is_mailed_without_anything_personal(deck, mailoutbox):
    run = make_run(deck, Status.FAILED, finished_at=timezone.now(),
                   error="WorkerLostError: killed\nTraceback ...")

    assert alerts.check() == ["failed_runs"]

    mail = mailoutbox[0]
    assert mail.to == ["hello@example.ch"]
    assert "1 simulation run(s) failed" in mail.subject
    assert str(run.pk) in mail.body
    assert f"/admin/simulations/simulationrun/{run.pk}/change/" in mail.body
    assert "WorkerLostError: killed" in mail.body
    assert "Traceback" not in mail.body
    assert "private@example.com" not in mail.body
    assert "Secret Deck Name" not in mail.body


def test_one_mail_a_day_per_kind(deck, mailoutbox):
    now = timezone.now()
    make_run(deck, Status.FAILED, finished_at=now)
    alerts.check(now)
    make_run(deck, Status.FAILED, finished_at=now + timedelta(hours=2))

    assert alerts.check(now + timedelta(hours=3)) == []
    # A day later the second failure is mailed, and only it.
    assert alerts.check(now + timedelta(days=1, minutes=1)) == ["failed_runs"]
    assert "1 simulation run(s) failed" in mailoutbox[1].subject


def test_a_failure_before_the_last_mail_is_not_mailed_again(deck, mailoutbox):
    now = timezone.now()
    make_run(deck, Status.FAILED, finished_at=now)
    alerts.check(now)

    assert alerts.check(now + timedelta(days=2)) == []


@pytest.mark.parametrize(("status", "field", "age", "stuck"), [
    (Status.PENDING, "created_at", timedelta(minutes=11), True),
    (Status.PENDING, "created_at", timedelta(minutes=5), False),
    (Status.RUNNING, "started_at", timedelta(minutes=31), True),
    (Status.RUNNING, "started_at", timedelta(minutes=20), False),
])
def test_a_run_that_does_not_move_is_mailed(deck, mailoutbox, status, field, age, stuck):
    make_run(deck, status, **{field: timezone.now() - age})

    assert alerts.check() == (["stuck_runs"] if stuck else [])


def test_a_failed_catalogue_job_is_mailed(mailoutbox):
    BulkImport.objects.create(kind="oracle_cards", scryfall_updated_at=timezone.now(),
                              status=BulkImport.Status.FAILED, message="nightly job: HTTP 503")

    assert alerts.check() == ["catalogue"]
    assert "HTTP 503" in mailoutbox[0].body


def test_a_catalogue_load_killed_halfway_is_mailed(mailoutbox):
    row = BulkImport.objects.create(kind="oracle_tags", scryfall_updated_at=timezone.now())
    BulkImport.objects.filter(pk=row.pk).update(started_at=timezone.now() - timedelta(hours=2))

    assert alerts.check() == ["catalogue"]


def test_without_a_recipient_it_only_logs(deck, mailoutbox, settings, caplog):
    settings.ALERT_EMAIL = ""
    make_run(deck, Status.FAILED, finished_at=timezone.now())

    assert alerts.check() == []
    assert mailoutbox == []
    assert "ALERT_EMAIL unset" in caplog.text


def test_a_failed_send_is_tried_again_next_hour(deck, mailoutbox, monkeypatch):
    make_run(deck, Status.FAILED, finished_at=timezone.now())

    def down(*args, **kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr(alerts, "send_mail", down)
    with pytest.raises(ConnectionRefusedError):
        alerts.check()
    monkeypatch.undo()

    assert alerts.check() == ["failed_runs"]


def test_the_check_runs_every_hour():
    entry = settings.CELERY_BEAT_SCHEDULE["alerts"]

    assert entry["task"] == tasks.alerts.name
    assert entry["schedule"].hour == set(range(24))
    assert len(entry["schedule"].minute) == 1
