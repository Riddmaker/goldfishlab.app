"""Load protection before the launch (P1).

* A guest gets one free summary - a new upload no longer hands out another.
* A refused import no longer deletes the guest's deck.
* At most SUMMARIES_PER_DAY summaries a day, guests at most their share; at
  the ceiling nobody is charged for one, and the report says so.
* A summary whose call ran out of time, or whose worker died, is closed and
  its run given back instead of showing "being written" for ever.
* A waiting run says how many runs are before it.

Mistral is never called: `urlopen` is a stand-in, as in
test_deck_summary_written.py, whose fixtures these tests share.
"""

# The fixtures shared below are parameters of the tests that use them.
# ruff: noqa: F811

import urllib.error
from datetime import timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from billing import quotas
from billing.models import UsageRecord
from core import alerts
from decks import services as deck_services
from decks.models import Deck
from simulations import budget, mistral, services, summary, tasks
from simulations.models import DeckSummary, SimulationRun, SummaryDay
from tests.test_deck_summary_written import (  # noqa: F401 - shared fixtures
    ARCHIDEKT_CSV,
    _answer,
    _Response,
    _start,
    deck,
    finished,
    key,
    no_runs,
    owner,
    queued,
)
from tests.test_guests import _no_workers, upload  # noqa: F401 - shared fixture

pytestmark = pytest.mark.django_db

User = get_user_model()
RUNS = UsageRecord.Metric.RUNS_STARTED
SUMMARIES = UsageRecord.Metric.SUMMARIES_WRITTEN


def _member(email):
    return User.objects.create_user(email=email, password="pw-test-1234")


def _deck_of(user):
    return deck_services.import_deck(
        owner=user, raw=ARCHIDEKT_CSV.read_bytes(), name="Deck", filename="sample.csv",
    ).deck


def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(mistral, "RETRY_WAIT", 0)


# --- the guest's one summary ---------------------------------------------------------


def test_a_new_upload_does_not_give_a_guest_another_summary(key, client, catalogue):
    upload(client)
    guest = User.objects.get(is_guest=True)
    first = DeckSummary.objects.get(deck__owner=guest)
    first.status = DeckSummary.Status.DONE
    first.save()
    assert quotas.used(guest, SUMMARIES) == 1

    upload(client, "1 Sol Ring\n1 Arcane Signet\n35 Swamp\n")
    upload(client)

    assert not DeckSummary.objects.filter(deck__owner=guest).exists(), \
        "the old deck went with its summary, and no new one was written"
    assert SummaryDay.objects.get().guests == 1


def test_a_guest_whose_summary_failed_gets_it_back(key, owner, deck, no_runs, queued,
                                                   monkeypatch):
    owner.is_guest = True
    owner.save()
    _start(owner, deck)
    row = DeckSummary.objects.get(deck=deck)
    _no_retry_wait(monkeypatch)

    def down(request, timeout):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(mistral.urllib.request, "urlopen", down)
    assert tasks.write_summary(str(row.pk)) == DeckSummary.Status.FAILED

    assert quotas.used(owner, SUMMARIES) == 0
    assert summary.due(owner, deck), "it may be tried again"


def test_a_refused_import_keeps_the_guests_deck(client, catalogue):
    upload(client)
    guest = User.objects.get(is_guest=True)
    kept = guest.decks.get()
    limit = quotas.plan_for(guest).max_imports_per_month
    quotas.consume(guest, UsageRecord.Metric.IMPORTS, limit - quotas.used(
        guest, UsageRecord.Metric.IMPORTS))

    response = upload(client, "1 Sol Ring\n36 Swamp\n")

    assert response.status_code == 200, "the form again, with the refusal"
    assert Deck.objects.filter(pk=kept.pk).exists()


# --- the daily budget -----------------------------------------------------------------


def test_the_budget_never_hands_out_more_than_its_ceiling(settings):
    settings.SUMMARIES_PER_DAY = 3
    settings.GUEST_SUMMARIES_PER_DAY = 2

    taken = [budget.reserve(is_guest=True) for _ in range(3)]
    assert taken[:2] == [budget.today()] * 2 and taken[2] is None, "guests' share"
    assert budget.reserve(is_guest=False) == budget.today()
    assert budget.reserve(is_guest=False) is None, "the whole day's"
    assert budget.counts() == (2, 1)


def test_at_the_ceiling_a_run_starts_without_a_summary_and_costs_one_run(
        key, settings, owner, deck, no_runs, queued):
    settings.SUMMARIES_PER_DAY = 1
    _start(owner, deck)
    other = _member("second@example.com")
    other_deck = _deck_of(other)

    _start(other, other_deck)

    assert quotas.used(owner, RUNS) == 2, "run plus summary"
    assert quotas.used(other, RUNS) == 1, "no summary, nothing charged for one"
    assert not DeckSummary.objects.filter(deck=other_deck).exists()
    written = summary.state(other, other_deck)
    assert written["paused"] and not written["offer"]


def test_guests_cannot_spend_the_members_share(key, settings, owner, deck, no_runs, queued):
    settings.GUEST_SUMMARIES_PER_DAY = 0
    guest = _member("guest@example.com")
    guest.is_guest = True
    guest.save()

    _start(guest, _deck_of(guest))
    _start(owner, deck)

    assert not DeckSummary.objects.filter(deck__owner=guest).exists()
    assert DeckSummary.objects.filter(deck=deck).exists()


def test_the_paused_report_says_so(key, settings, owner, finished, client):
    settings.SUMMARIES_PER_DAY = 0
    client.force_login(owner)

    written = summary.state(owner, finished.deck)
    page = client.get(finished.get_absolute_url()).content.decode()

    assert written["paused"] and not written["offer"] and not written["short"]
    assert "paused until midnight" in page


def test_a_call_that_never_answered_gives_its_slot_back(key, owner, deck, no_runs, queued,
                                                         monkeypatch):
    _start(owner, deck)
    row = DeckSummary.objects.get(deck=deck)
    assert budget.counts() == (0, 1)
    _no_retry_wait(monkeypatch)

    def down(request, timeout):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(mistral.urllib.request, "urlopen", down)
    tasks.write_summary(str(row.pk))

    assert budget.counts() == (0, 0)
    assert quotas.used(owner, RUNS) == 1, "the summary's run came back"


def test_an_answer_that_did_not_check_out_keeps_its_slot(key, owner, deck, no_runs, queued,
                                                         monkeypatch):
    _start(owner, deck)
    row = DeckSummary.objects.get(deck=deck)
    monkeypatch.setattr(mistral.urllib.request, "urlopen",
                        lambda request, timeout: _Response(_answer("not an object")))

    assert tasks.write_summary(str(row.pk)) == DeckSummary.Status.FAILED

    assert budget.counts() == (0, 1), "Mistral billed it"
    assert quotas.used(owner, RUNS) == 1


def test_the_operator_hears_of_a_nearly_spent_budget(settings):
    settings.SUMMARIES_PER_DAY = 10
    now = timezone.now()
    SummaryDay.objects.create(day=timezone.localdate(now), guests=5, members=2)
    assert alerts.summary_budget(now) is None

    SummaryDay.objects.filter(day=timezone.localdate(now)).update(members=3)

    problem = alerts.summary_budget(now)
    assert problem is not None and "8 of 10" in problem.lines[0]


# --- summaries that never come back ---------------------------------------------------


def test_running_out_of_time_closes_the_summary_and_refunds(key, owner, deck, no_runs,
                                                            queued, monkeypatch):
    _start(owner, deck)
    row = DeckSummary.objects.get(deck=deck)

    def slow(request, timeout):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(mistral.urllib.request, "urlopen", slow)

    assert tasks.write_summary(str(row.pk)) == DeckSummary.Status.FAILED
    row.refresh_from_db()
    assert row.status == DeckSummary.Status.FAILED
    assert quotas.used(owner, RUNS) == 1


def test_two_attempts_end_inside_the_soft_time_limit(settings):
    worst = 2 * mistral.TIMEOUT_SECONDS + mistral.RETRY_WAIT
    assert worst < settings.CELERY_TASK_SOFT_TIME_LIMIT - 15


def test_a_summary_whose_worker_died_is_closed_once(key, owner, deck, no_runs, queued):
    _start(owner, deck)
    DeckSummary.objects.update(updated_at=timezone.now() - timedelta(minutes=11))

    assert summary.close_stale() == 1
    assert summary.close_stale() == 0

    assert DeckSummary.objects.get(deck=deck).status == DeckSummary.Status.FAILED
    assert quotas.used(owner, RUNS) == 1, "refunded exactly once"
    assert budget.counts() == (0, 1), "whether Mistral billed it is unknown"


def test_a_fresh_summary_is_left_alone(key, owner, deck, no_runs, queued):
    _start(owner, deck)

    assert summary.close_stale() == 0
    assert DeckSummary.objects.get(deck=deck).status == DeckSummary.Status.PENDING


# --- the queue --------------------------------------------------------------------------


def _run(owner, deck, games, status=SimulationRun.Status.PENDING, ago=0):
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=games, turns=6,
                                       seed=1, status=status)
    SimulationRun.objects.filter(pk=run.pk).update(
        created_at=timezone.now() - timedelta(seconds=ago))
    run.refresh_from_db()
    return run


def test_a_waiting_run_counts_the_runs_before_it_on_its_queue(owner, deck):
    _run(owner, deck, 2_000, SimulationRun.Status.RUNNING, ago=60)
    _run(owner, deck, 2_000, ago=30)
    _run(owner, deck, 100_000, ago=20)  # the long queue: not in the way
    mine = _run(owner, deck, 2_000, ago=10)
    _run(owner, deck, 2_000, ago=0)  # after mine

    queue = services.queue_ahead(mine)

    assert queue.ahead == 2
    assert queue.seconds > 0


def test_the_progress_fragment_names_the_runs_ahead(owner, deck, client):
    _run(owner, deck, 2_000, ago=30)
    mine = _run(owner, deck, 2_000, ago=10)
    client.force_login(owner)

    page = client.get(reverse("simulations:progress", args=[mine.pk]))

    assert page.status_code == 200
    assert "1 run ahead of yours" in page.content.decode()
