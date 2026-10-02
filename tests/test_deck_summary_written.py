"""The deck summary's written part (phase 10 H, T6.1, T6.2, T6.4, T6.5).

Mistral is never called: `urlopen` and the task are stand-ins. What is tested
is everything around the call - what goes into the prompt, what comes back
and is allowed onto the page, when a summary is written, what it costs, and
that a failure gives the run back exactly once.
"""

import io
import json
import urllib.error
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from billing.models import UsageRecord
from billing.quotas import used
from decks import services as deck_services
from simulations import mistral, services, summary, tasks
from simulations.engine import adapter
from simulations.models import CardAnnotation, DeckSummary, SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCHIDEKT_CSV = FIXTURES / "archidekt_sample.csv"
RUNS = UsageRecord.Metric.RUNS_STARTED

GOOD = {
    "feel": "A **graveyard** deck that wants long games. See https://example.com now.",
    "strengths": ["+ Plenty of ramp.", "Strong recursion.", "c", "d", "e", "f"],
    "weaknesses": ["- Few ways to draw cards."],
    "tactics": "Keep hands with two lands and a ramp spell.",
}


class _Response(io.BytesIO):
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _answer(content: dict) -> bytes:
    return json.dumps({
        "model": "mistral-small-2603",
        "choices": [{"index": 0, "message": {"role": "assistant",
                                             "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
    }).encode()


@pytest.fixture
def key(settings):
    settings.MISTRAL_API_KEY = "test-key-not-real"
    return settings


@pytest.fixture
def queued(monkeypatch):
    """The summaries that would have been sent to a worker."""
    sent = []
    monkeypatch.setattr(tasks.write_summary, "delay", lambda pk: sent.append(pk))
    return sent


@pytest.fixture
def no_runs(monkeypatch):
    """No Redis and no Celery for the run itself."""
    monkeypatch.setattr(services, "_take_slot", lambda owner, limit: True)
    monkeypatch.setattr(services, "_dispatch", lambda run, tasks_module: None)


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="summary@example.com", password="pw-test-1234")


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="My secret deck name",
        filename="sample.csv",
    ).deck


def _start(owner, deck):
    return services.start_run(owner=owner, deck=deck, games=20, turns=2)


# --- the client ------------------------------------------------------------------


def test_the_client_refuses_without_a_key(settings):
    settings.MISTRAL_API_KEY = ""

    with pytest.raises(mistral.MistralError, match="not configured"):
        mistral.complete([], max_tokens=10)


def test_the_client_sends_json_mode_and_reads_the_answer(key, monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["body"] = json.loads(request.data)
        seen["auth"] = request.get_header("Authorization")
        seen["url"] = request.full_url
        return _Response(_answer(GOOD))

    monkeypatch.setattr(mistral.urllib.request, "urlopen", urlopen)
    completion = mistral.complete([{"role": "user", "content": "hi"}], max_tokens=50)

    assert seen["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["max_tokens"] == 50
    assert seen["auth"] == "Bearer test-key-not-real"
    assert json.loads(completion.content)["tactics"] == GOOD["tactics"]
    assert (completion.prompt_tokens, completion.completion_tokens) == (1200, 300)


def test_the_client_retries_once_and_never_names_the_key(key, monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)

    monkeypatch.setattr(mistral.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mistral.time, "sleep", lambda seconds: None)

    with pytest.raises(mistral.MistralError) as error:
        mistral.complete([], max_tokens=10)

    assert len(calls) == 2
    assert "503" in str(error.value)
    assert "test-key" not in str(error.value)


def test_the_client_does_not_retry_a_refusal(key, monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)

    monkeypatch.setattr(mistral.urllib.request, "urlopen", urlopen)

    with pytest.raises(mistral.MistralError):
        mistral.complete([], max_tokens=10)
    assert len(calls) == 1


# --- what goes in, what comes out --------------------------------------------------


def test_the_prompt_holds_the_deck_and_nothing_about_the_person(owner, deck):
    facts = summary.facts(deck, adapter.readings(deck))
    text = json.dumps(summary.messages(facts))

    assert "My secret deck name" not in text
    assert owner.email not in text
    assert facts["cards"], "the cards are the point"
    assert facts["cards_in_library"] == sum(card["count"] for card in facts["cards"])
    odds = facts["opening_hand_of_7"]["chance_of_exactly_n_lands_percent"]
    assert sum(odds.values()) == pytest.approx(100, abs=0.5)


def test_an_answer_is_checked_before_it_is_kept():
    checked = summary.parse(json.dumps(GOOD))

    assert "**" not in checked["feel"]
    assert "example.com" not in checked["feel"]
    assert len(checked["strengths"]) == summary.POINTS_MAX
    assert checked["strengths"][0] == "Plenty of ramp.", "the page draws its own +"
    assert checked["weaknesses"] == ["Few ways to draw cards."]


def test_a_long_answer_is_cut_at_a_word():
    long = dict(GOOD, feel="word " * 400)

    feel = summary.parse(json.dumps(long))["feel"]

    assert len(feel) <= summary.FEEL_MAX + 1
    assert feel.endswith("word…")


@pytest.mark.parametrize("content", [
    "not json", "[]", json.dumps({"feel": 3}),
    json.dumps({"feel": "x", "strengths": [], "weaknesses": [], "tactics": ""}),
    json.dumps(dict(GOOD, strengths="not a list")),
])
def test_an_unusable_answer_is_refused(content):
    with pytest.raises(ValueError):
        summary.parse(content)


# --- the fingerprint ------------------------------------------------------------------


def test_the_fingerprint_moves_with_the_cards_and_the_annotations(owner, deck):
    first = summary.fingerprint(deck)
    assert summary.fingerprint(deck) == first

    entry = deck.entries.first()
    entry.quantity += 1
    entry.save()
    second = summary.fingerprint(deck)
    assert second != first

    CardAnnotation.objects.create(owner=owner, deck=deck, oracle_card=entry.oracle_card,
                                  overrides={"tags": ["ramp"]})
    assert summary.fingerprint(deck) != second


# --- when it is written, and what it costs ----------------------------------------------


def test_without_a_key_a_run_costs_one_and_writes_nothing(owner, deck, no_runs, queued,
                                                           django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        _start(owner, deck)

    assert used(owner, RUNS) == 1
    assert not DeckSummary.objects.exists()
    assert queued == []


def test_a_new_deck_costs_two_and_queues_its_summary(key, owner, deck, no_runs, queued,
                                                     django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        _start(owner, deck)

    row = DeckSummary.objects.get(deck=deck)
    assert used(owner, RUNS) == 2
    assert row.status == DeckSummary.Status.PENDING
    assert row.charged
    assert queued == [str(row.pk)]


def test_an_unchanged_deck_reuses_its_summary_for_free(key, owner, deck, no_runs, queued,
                                                       django_capture_on_commit_callbacks):
    DeckSummary.objects.create(deck=deck, fingerprint=summary.fingerprint(deck),
                               status=DeckSummary.Status.DONE, content=GOOD)

    with django_capture_on_commit_callbacks(execute=True):
        _start(owner, deck)

    assert used(owner, RUNS) == 1
    assert queued == []


def test_a_changed_deck_gets_a_new_one(key, owner, deck, no_runs, queued,
                                       django_capture_on_commit_callbacks):
    DeckSummary.objects.create(deck=deck, fingerprint="an older deck",
                               status=DeckSummary.Status.DONE, content=GOOD)

    with django_capture_on_commit_callbacks(execute=True):
        _start(owner, deck)

    row = DeckSummary.objects.get(deck=deck)
    assert used(owner, RUNS) == 2
    assert row.status == DeckSummary.Status.PENDING
    assert row.content == GOOD, "the old text stays until the new one replaces it"


def test_with_one_run_left_the_run_goes_alone(key, owner, deck, no_runs, queued):
    from billing.quotas import consume, plan_for

    consume(owner, RUNS, plan_for(owner).max_runs_per_month - 1)

    _start(owner, deck)

    assert used(owner, RUNS) == plan_for(owner).max_runs_per_month
    assert not DeckSummary.objects.exists()


def test_switched_off_nothing_is_written(key, owner, deck, no_runs, queued):
    owner.deck_summaries = False
    owner.save()

    _start(owner, deck)

    assert used(owner, RUNS) == 1
    assert not DeckSummary.objects.exists()


def test_a_guest_gets_one_summary_free(key, owner, deck, no_runs, queued, catalogue):
    owner.is_guest = True
    owner.save()

    _start(owner, deck)
    assert used(owner, RUNS) == 1, "P7: not taken from the trial runs"
    row = DeckSummary.objects.get(deck=deck)
    assert not row.charged

    row.status = DeckSummary.Status.DONE
    row.fingerprint = "an older deck"
    row.save()
    _start(owner, deck)
    assert DeckSummary.objects.get(deck=deck).status == DeckSummary.Status.DONE, "only one"


def test_a_summary_being_written_is_not_charged_twice(key, owner, deck, no_runs, queued):
    _start(owner, deck)
    _start(owner, deck)

    assert used(owner, RUNS) == 3
    assert DeckSummary.objects.count() == 1


# --- the task ----------------------------------------------------------------------------


def _pending(deck, charged=True):
    return DeckSummary.objects.create(deck=deck, fingerprint=summary.fingerprint(deck),
                                      status=DeckSummary.Status.PENDING, charged=charged)


def test_the_task_keeps_a_checked_answer(key, deck, monkeypatch):
    row = _pending(deck)
    monkeypatch.setattr(mistral.urllib.request, "urlopen",
                        lambda request, timeout: _Response(_answer(GOOD)))

    assert tasks.write_summary(str(row.pk)) == DeckSummary.Status.DONE

    row.refresh_from_db()
    assert row.content["weaknesses"] == ["Few ways to draw cards."]
    assert row.model_name == "mistral-small-2603"
    assert row.prompt_version == summary.PROMPT_VERSION
    assert (row.prompt_tokens, row.completion_tokens) == (1200, 300)


def test_a_failed_summary_gives_its_run_back_once(key, owner, deck, monkeypatch):
    from billing.quotas import consume

    consume(owner, RUNS, 2)
    row = _pending(deck)
    monkeypatch.setattr(mistral.urllib.request, "urlopen",
                        lambda request, timeout: _Response(b'{"choices": []}'))

    assert tasks.write_summary(str(row.pk)) == DeckSummary.Status.FAILED
    assert tasks.write_summary(str(row.pk)) == "nothing to write"

    row.refresh_from_db()
    assert used(owner, RUNS) == 1
    assert row.status == DeckSummary.Status.FAILED
    assert not row.charged


# --- the page ---------------------------------------------------------------------------


@pytest.fixture
def finished(owner, deck, monkeypatch):
    monkeypatch.setattr(services, "_redis", lambda: _NoRedis())
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=20, turns=2, seed=7)
    tasks.finalize_run([tasks.simulate_chunk(str(run.pk), 0, 20)], str(run.pk))
    return run


class _NoRedis:
    def decr(self, key):
        return 0

    def set(self, *args, **kwargs):
        return True


def _page(client, owner, run):
    client.force_login(owner)
    return client.get(reverse("simulations:detail", args=[run.pk])).content.decode()


def test_the_text_is_shown_escaped_in_k9_order(key, client, owner, deck, finished):
    content = dict(summary.parse(json.dumps(GOOD)), tactics="Cast <script>x</script> early.")
    DeckSummary.objects.create(deck=deck, fingerprint=summary.fingerprint(deck),
                               status=DeckSummary.Status.DONE, content=content)

    body = _page(client, owner, finished)
    block = body[body.index('id="summary"'):body.index('id="advanced"')]

    order = [">Feel<", ">Mechanisms<", ">Strengths<", ">Weaknesses<", ">Tactics<"]
    assert [block.index(title) for title in order] == sorted(block.index(t) for t in order)
    assert "&lt;script&gt;" in block and "<script>x" not in block
    assert "Written by Mistral AI. It can be wrong." in block
    assert "Write a summary" not in block, "it is current"


def test_a_summary_being_written_polls(key, client, owner, deck, finished):
    _pending(deck)

    body = _page(client, owner, finished)
    fragment = client.get(reverse("simulations:summary", args=[finished.pk]),
                          HTTP_HX_REQUEST="true")

    assert "Writing your deck summary" in body
    assert reverse("simulations:summary", args=[finished.pk]) in body
    assert "HX-Refresh" not in fragment.headers

    DeckSummary.objects.filter(deck=deck).update(status=DeckSummary.Status.DONE)
    done = client.get(reverse("simulations:summary", args=[finished.pk]),
                      HTTP_HX_REQUEST="true")
    assert done.headers["HX-Refresh"] == "true"


def test_an_outdated_summary_offers_a_new_one_for_one_run(key, client, owner, deck,
                                                          finished, queued):
    DeckSummary.objects.create(deck=deck, fingerprint="older", status=DeckSummary.Status.DONE,
                               content=summary.parse(json.dumps(GOOD)))
    before = used(owner, RUNS)

    body = _page(client, owner, finished)
    assert "Written for an earlier version of this deck." in body
    assert "Write a summary for this deck - uses 1 of your" in body

    response = client.post(reverse("simulations:write_summary", args=[finished.pk]))
    client.post(reverse("simulations:write_summary", args=[finished.pk]))

    assert response.status_code == 302
    assert used(owner, RUNS) == before + 1, "a second click does not charge again"
    assert DeckSummary.objects.get(deck=deck).status == DeckSummary.Status.PENDING


def test_hiding_summaries_takes_the_block_away(key, client, owner, deck, finished):
    _page(client, owner, finished)

    client.post(reverse("simulations:summary_switch"),
                {"on": "0", "next": finished.get_absolute_url()})
    hidden = client.get(reverse("simulations:detail", args=[finished.pk])).content.decode()
    plans = client.get(reverse("billing:plans")).content.decode()

    assert 'id="summary"' not in hidden
    assert "Show deck summaries again" in plans

    client.post(reverse("simulations:summary_switch"), {"on": "1"})
    owner.refresh_from_db()
    assert owner.deck_summaries


def test_the_switch_does_not_redirect_off_the_site(client, owner):
    client.force_login(owner)

    response = client.post(reverse("simulations:summary_switch"),
                           {"on": "0", "next": "https://evil.example/"})

    assert response.url == reverse("billing:plans")


def test_another_users_run_cannot_buy_a_summary(key, client, finished):
    stranger = User.objects.create_user(email="x@example.com", password="pw-test-1234")
    client.force_login(stranger)

    response = client.post(reverse("simulations:write_summary", args=[finished.pk]))

    assert response.status_code == 404


def test_without_a_key_the_block_is_the_mechanisms_alone(client, owner, finished):
    body = _page(client, owner, finished)
    block = body[body.index('id="summary"'):body.index('id="advanced"')]

    assert "Mechanisms" in block
    assert "Mistral" not in block
    assert "Hide summaries" not in block


def test_the_export_holds_the_summaries(owner, deck):
    from accounts import privacy

    DeckSummary.objects.create(deck=deck, fingerprint="x", status=DeckSummary.Status.DONE,
                               content=GOOD)

    data = privacy.export(owner)

    assert data["deck_summaries"][0]["content"] == GOOD
    assert data["account"]["deck_summaries"] is True
