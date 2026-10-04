"""Phase 12 J18: the nightly catalogue job.

Scryfall is never called: `sets_fingerprint` is faked, and the bulk files come
from the committed fixtures (as in test_cards_ingest). What is pinned is the
rule - fetch only when Scryfall's sets changed, or on Tuesdays - and that every
failure leaves a row the alerts can find.
"""

import io
import json
from datetime import date
from pathlib import Path

import pytest
from django.conf import settings

from cards import ingest, refresh, scryfall, tasks
from cards.models import BulkImport, DerivedProfile, OracleCard

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CARDS = FIXTURES / "oracle_cards_sample.jsonl.gz"
TAGS = FIXTURES / "oracle_tags_sample.jsonl.gz"

MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)


@pytest.fixture
def scryfall_fake(monkeypatch):
    """Scryfall as the job sees it: a fingerprint, and the fixture files."""
    state = {"fingerprint": "1053:118470", "loads": [], "fail": None}

    def fingerprint():
        if state["fail"]:
            raise scryfall.ScryfallError(state["fail"])
        return state["fingerprint"]

    real_cards, real_tags = ingest.ingest_cards, ingest.ingest_tags

    def cards(**options):
        state["loads"].append("cards")
        options["source"] = CARDS
        return real_cards(**options)

    def tags(**options):
        state["loads"].append("tags")
        options["source"] = TAGS
        return real_tags(**options)

    monkeypatch.setattr(scryfall, "sets_fingerprint", fingerprint)
    monkeypatch.setattr(ingest, "ingest_cards", cards)
    monkeypatch.setattr(ingest, "ingest_tags", tags)
    return state


def test_the_first_night_loads_everything(scryfall_fake):
    line = refresh.nightly(MONDAY)

    assert scryfall_fake["loads"] == ["cards", "tags"]
    assert DerivedProfile.objects.count() == OracleCard.objects.count() > 50
    assert BulkImport.objects.filter(kind="oracle_cards", status="ok").get().sets_fingerprint == (
        "1053:118470"
    )
    assert "sets changed" in line


def test_nothing_new_fetches_nothing(scryfall_fake):
    refresh.nightly(MONDAY)
    scryfall_fake["loads"].clear()

    line = refresh.nightly(MONDAY)

    assert scryfall_fake["loads"] == []
    assert "nothing new" in line


def test_a_new_set_or_previews_load_again(scryfall_fake):
    refresh.nightly(MONDAY)
    scryfall_fake["loads"].clear()
    scryfall_fake["fingerprint"] = "1053:118500"

    refresh.nightly(MONDAY)

    assert scryfall_fake["loads"] == ["cards", "tags"]
    newest = BulkImport.objects.filter(kind="oracle_cards", status="ok").first()
    assert newest.sets_fingerprint == "1053:118500"


def test_tuesday_loads_even_without_new_sets(scryfall_fake):
    """Errata, bans and Tagger edits change no count."""
    refresh.nightly(MONDAY)
    scryfall_fake["loads"].clear()

    line = refresh.nightly(TUESDAY)

    assert scryfall_fake["loads"] == ["cards", "tags"]
    assert "weekly" in line


def test_a_hand_run_ingest_is_checked_again_the_next_night(scryfall_fake):
    """A row without a fingerprint (ingest_scryfall by hand) is not "unchanged"."""
    ingest.ingest_cards(source=CARDS)
    scryfall_fake["loads"].clear()

    refresh.nightly(MONDAY)

    assert scryfall_fake["loads"] == ["cards", "tags"]


def _answer(monkeypatch, payload):
    body = json.dumps(payload).encode()
    monkeypatch.setattr(scryfall, "_open", lambda url, **_: io.BytesIO(body))


def test_the_fingerprint_counts_sets_and_printings(monkeypatch):
    _answer(monkeypatch, {"has_more": False, "data": [{"card_count": 3}, {"card_count": 4}, {}]})

    assert scryfall.sets_fingerprint() == "3:7"


@pytest.mark.parametrize("payload", [{"data": []}, {"has_more": True, "data": [{"card_count": 1}]}])
def test_a_strange_sets_answer_is_an_error_not_a_wrong_count(monkeypatch, payload):
    _answer(monkeypatch, payload)

    with pytest.raises(scryfall.ScryfallError):
        scryfall.sets_fingerprint()


# --- the task --------------------------------------------------------------------


def test_a_failed_pre_check_leaves_a_failed_row(scryfall_fake):
    scryfall_fake["fail"] = "HTTP 503"

    with pytest.raises(scryfall.ScryfallError):
        tasks.refresh_catalogue()

    row = BulkImport.objects.get()
    assert row.status == BulkImport.Status.FAILED
    assert "HTTP 503" in row.message


def test_a_failed_load_is_not_recorded_twice(scryfall_fake, monkeypatch):
    """`ingest._run` already wrote the FAILED row; the task adds none."""

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(scryfall, "stream_jsonl", broken)

    with pytest.raises(RuntimeError):
        tasks.refresh_catalogue()

    assert BulkImport.objects.filter(status=BulkImport.Status.FAILED).count() == 1


def test_two_refreshes_never_run_side_by_side(scryfall_fake):
    from django.core.cache import cache

    cache.add(tasks.LOCK, True)

    assert tasks.refresh_catalogue().startswith("skipped")
    assert scryfall_fake["loads"] == []


def test_the_lock_is_released_after_a_failure(scryfall_fake):
    from django.core.cache import cache

    scryfall_fake["fail"] = "HTTP 503"
    with pytest.raises(scryfall.ScryfallError):
        tasks.refresh_catalogue()

    assert cache.get(tasks.LOCK) is None


def test_the_job_runs_at_half_past_midnight_on_the_long_queue():
    entry = settings.CELERY_BEAT_SCHEDULE["cards-refresh"]
    task = tasks.refresh_catalogue

    assert entry["task"] == task.name
    assert entry["schedule"].hour == {0} and entry["schedule"].minute == {30}
    assert entry["options"]["queue"] == task.queue == "sim_long"


def test_the_job_is_never_handed_out_twice():
    """Late acks plus a load longer than the visibility timeout would run it twice."""
    task = tasks.refresh_catalogue
    visibility = settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]

    assert task.acks_late is False
    assert task.soft_time_limit < task.time_limit
    assert task.time_limit > visibility  # why acks_late has to be off
