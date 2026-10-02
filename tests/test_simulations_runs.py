"""Phase 3: running a deck in the background and reporting what happened.

What these tests are really for, in order of how much they matter:

1. **The numbers are right.** `report.hypergeometric` is checked against scipy,
   which is a test-only dependency - the production image computes the same
   thing with `math.comb` and must agree with it exactly.
2. **Nothing is lost or double-counted.** Progress adds up, a cancelled run
   refunds its quota, a failed one does too, and neither happens twice.
3. **Ownership.** Every run lookup is filtered by owner at the source, so
   somebody else's run is a 404 and not a permission error.
4. **Polling stops.** The progress fragment carries its own trigger; when the
   run is finished the fragment comes back without it.
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from scipy.stats import hypergeom

from billing.models import Plan, UsageRecord
from billing.quotas import period_start
from decks import services as deck_services
from simulations import report, services, tasks
from simulations.engine import runner
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCHIDEKT_CSV = FIXTURES / "archidekt_sample.csv"
PASSWORD = "pw-for-test-only"


class FakeRedis:
    """Enough Redis for the concurrency counter, and no network.

    The counter is the part worth testing - INCR is what makes two simultaneous
    enqueues race safely - so it is exercised for real rather than stubbed out.
    """

    def __init__(self):
        self.values = {}

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def decr(self, key):
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    def expire(self, key, seconds):
        return True

    def set(self, key, value, ex=None):
        self.values[key] = value
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(services, "_redis", lambda: client)
    return client


@pytest.fixture
def no_dispatch(monkeypatch):
    """Record what would have been queued instead of queueing it."""
    queued = []
    monkeypatch.setattr(
        services, "_dispatch", lambda run, tasks_module: queued.append(run)
    )
    return queued


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="owner@example.com", password=PASSWORD)


@pytest.fixture
def deck(owner):
    outcome = deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer", filename="sample.csv"
    )
    return outcome.deck


@pytest.fixture
def run(owner, deck):
    return SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=40, turns=2, seed=99
    )


# --- the mathematics -------------------------------------------------------


def test_the_hypergeometric_matches_scipy():
    """The most load-bearing number on the report page.

    Production cannot have scipy - 90 MB on a 128 MiB cloudlet - so the
    hypergeometric is computed with `math.comb`. This is the test that says
    the cheap version is the same version.
    """
    for lands_in_hand in range(8):
        ours = report.hypergeometric(lands_in_hand, 99, 35, 7)
        theirs = 100 * hypergeom.pmf(lands_in_hand, 99, 35, 7)
        assert ours == pytest.approx(theirs, abs=1e-9)


def test_the_hypergeometric_refuses_impossible_hands():
    """Zero, not a negative combination or a crash."""
    assert report.hypergeometric(7, 99, 3, 7) == 0.0
    assert report.hypergeometric(0, 99, 99, 7) == 0.0
    assert report.hypergeometric(1, 0, 0, 7) == 0.0


def test_a_simulated_deck_agrees_with_the_exact_distribution():
    """The claim the report page makes, asserted rather than displayed.

    The first seven cards have an exact closed-form distribution, so the
    simulation and the hypergeometric have to land on the same numbers. This is
    the comparison the page shows a reader, run here against real games.

    It compares the *first seven*, not the hand that was kept. An earlier
    version of this test compared the kept hand and failed by eleven percentage
    points - correctly, because the mulligan rule throws the tails back and
    redistributes them. That is a fact about the heuristic, not an error, and
    the report now shows the two distributions separately for exactly that
    reason.
    """
    from simulation import analysis

    result = analysis.run(4000, turns=1, seed=20260917)
    rows = report.opening_lands(result, 99, 35)

    for row in rows:
        assert row.share == pytest.approx(row.exact, abs=2.0), row.label


def test_the_kept_hands_are_not_the_dealt_hands():
    """The mulligan rule has to be visible in the numbers, or it is not working."""
    from simulation import analysis

    result = analysis.run(2000, turns=1, seed=7)
    dealt = {row.label: row.share for row in report.opening_lands(result, 99, 35)}
    kept = {row.label: row.share for row in report.kept_lands(result)}

    # Hands with no lands are dealt regularly and kept almost never.
    assert dealt.get("0", 0) > kept.get("0", 0)
    # The keepable middle is correspondingly over-represented in what is kept.
    assert kept["3"] > dealt["3"]


# --- chunk planning --------------------------------------------------------


def test_a_plan_adds_up_to_exactly_what_was_asked_for():
    """A run that quietly simulates 99,750 of 100,000 games lies in every number."""
    for games in (1, 999, 1000, 10_000, 100_000, 123_457):
        plan = runner.chunk_plan(games, turns=6)
        assert sum(plan) == games
        assert all(size > 0 for size in plan)


def test_a_plan_never_floods_the_queue():
    """One huge run must not push thousands of tasks past everybody else's."""
    assert len(runner.chunk_plan(10_000_000, turns=10)) <= runner.MAX_CHUNKS


def test_more_turns_means_smaller_chunks():
    """Cost is linear in turns, so a chunk has to shrink as turns grow."""
    assert runner.chunk_size_for(10) < runner.chunk_size_for(3)


def test_a_measured_rate_is_preferred_to_the_estimate():
    slow = runner.chunk_size_for(6, usec_per_game=10_000)
    fast = runner.chunk_size_for(6, usec_per_game=100)
    assert slow < fast


def test_a_run_of_no_games_is_refused():
    with pytest.raises(ValueError, match="at least one game"):
        runner.chunk_plan(0, turns=3)


# --- starting a run --------------------------------------------------------


def test_starting_a_run_debits_the_quota_once(owner, deck, fake_redis, no_dispatch):
    services.start_run(owner=owner, deck=deck, games=1000, turns=3)

    record = UsageRecord.objects.get(
        user=owner, metric=UsageRecord.Metric.RUNS_STARTED, period_start=period_start()
    )
    assert record.amount == 1


def test_a_run_over_the_plan_limit_is_refused(owner, deck, fake_redis, no_dispatch):
    plan = Plan.objects.get(is_default=True)

    with pytest.raises(services.SimulationRefused, match="at most"):
        services.start_run(
            owner=owner, deck=deck, games=plan.max_games_per_run + 1, turns=3
        )

    assert not SimulationRun.objects.exists()
    assert not UsageRecord.objects.filter(
        metric=UsageRecord.Metric.RUNS_STARTED
    ).exists()


def test_too_many_turns_is_refused(owner, deck, fake_redis, no_dispatch):
    plan = Plan.objects.get(is_default=True)

    with pytest.raises(services.SimulationRefused, match="turns"):
        services.start_run(owner=owner, deck=deck, games=100, turns=plan.max_turns + 1)


def test_one_user_cannot_occupy_every_worker(owner, deck, fake_redis, no_dispatch):
    """The concurrency cap, which is what the Redis counter exists for."""
    plan = Plan.objects.get(is_default=True)
    for _ in range(plan.max_concurrent_runs):
        services.start_run(owner=owner, deck=deck, games=100, turns=3)

    with pytest.raises(services.TooManyRuns):
        services.start_run(owner=owner, deck=deck, games=100, turns=3)


def test_a_refused_run_gives_its_slot_straight_back(owner, deck, fake_redis, no_dispatch):
    """Otherwise one refusal would lock the user out until the TTL expired."""
    plan = Plan.objects.get(is_default=True)
    for _ in range(plan.max_concurrent_runs):
        services.start_run(owner=owner, deck=deck, games=100, turns=3)
    with pytest.raises(services.TooManyRuns):
        services.start_run(owner=owner, deck=deck, games=100, turns=3)

    assert fake_redis.values[f"sim:active:{owner.pk}"] == plan.max_concurrent_runs


def test_big_runs_go_to_the_long_queue():
    """The fairness mechanism: a 100,000 game run must not sit in front of a 1,000."""
    assert services.queue_for(1_000) == services.SHORT_QUEUE
    assert services.queue_for(services.SHORT_QUEUE_MAX_GAMES) == services.SHORT_QUEUE
    assert services.queue_for(services.SHORT_QUEUE_MAX_GAMES + 1) == services.LONG_QUEUE


# --- the tasks -------------------------------------------------------------


def test_a_chunk_records_its_progress(run):
    tasks.simulate_chunk(str(run.pk), 0, 20)
    run.refresh_from_db()

    assert run.games_done == 20
    assert run.chunks_done == 1
    assert run.usec_per_game > 0


def test_progress_adds_up_across_chunks(run):
    tasks.simulate_chunk(str(run.pk), 0, 20)
    tasks.simulate_chunk(str(run.pk), 1, 20)
    run.refresh_from_db()

    assert run.games_done == run.games_total == 40
    assert run.chunks_done == 2


def test_a_cancelled_run_stops_before_the_next_chunk(run):
    """Cooperative cancellation: the flag is checked, nothing is killed."""
    SimulationRun.objects.filter(pk=run.pk).update(cancel_requested=True)

    assert tasks.simulate_chunk(str(run.pk), 0, 20) is tasks.SKIPPED
    run.refresh_from_db()
    assert run.games_done == 0


def test_finishing_a_run_stores_a_readable_result(run, fake_redis):
    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))
    run.refresh_from_db()

    assert run.status == SimulationRun.Status.DONE
    assert run.result["iterations"] == 40
    assert run.library_size > 0
    assert run.lands_total > 0
    assert run.finished_at is not None
    # The result has to survive the round trip the report page makes.
    assert report.build(run)["iterations"] == 40


def test_a_cancelled_run_refunds_the_quota(run, fake_redis, owner):
    UsageRecord.objects.create(
        user=owner,
        metric=UsageRecord.Metric.RUNS_STARTED,
        period_start=period_start(),
        amount=1,
    )
    SimulationRun.objects.filter(pk=run.pk).update(cancel_requested=True)
    run.refresh_from_db()

    tasks.finalize_run([tasks.SKIPPED], str(run.pk))
    run.refresh_from_db()

    assert run.status == SimulationRun.Status.CANCELLED
    assert UsageRecord.objects.get(
        user=owner, metric=UsageRecord.Metric.RUNS_STARTED
    ).amount == 0


def test_a_failed_run_refunds_the_quota_and_says_why(run, fake_redis, owner):
    UsageRecord.objects.create(
        user=owner,
        metric=UsageRecord.Metric.RUNS_STARTED,
        period_start=period_start(),
        amount=1,
    )

    tasks.fail_run(str(run.pk), None, RuntimeError("the engine fell over"), None)
    run.refresh_from_db()

    assert run.status == SimulationRun.Status.FAILED
    assert "fell over" in run.error
    assert UsageRecord.objects.get(
        user=owner, metric=UsageRecord.Metric.RUNS_STARTED
    ).amount == 0


def test_a_run_is_never_closed_twice(run, fake_redis, owner):
    """Two refunds for one run would hand back quota the user never spent."""
    UsageRecord.objects.create(
        user=owner,
        metric=UsageRecord.Metric.RUNS_STARTED,
        period_start=period_start(),
        amount=1,
    )
    tasks.fail_run(str(run.pk), None, RuntimeError("once"), None)
    tasks.fail_run(str(run.pk), None, RuntimeError("twice"), None)

    assert UsageRecord.objects.get(
        user=owner, metric=UsageRecord.Metric.RUNS_STARTED
    ).amount == 0


def test_two_workers_failing_together_refund_once(run, fake_redis, owner):
    """The race the old check could not see (2026-09-25 review).

    Each failing chunk holds its own copy of the row, loaded while the run was
    still RUNNING, so a check of `run.is_finished` on that copy passes for both.
    The database decides now: only the write that moves the row out of RUNNING
    refunds and frees the slot.
    """
    UsageRecord.objects.create(
        user=owner, metric=UsageRecord.Metric.RUNS_STARTED,
        period_start=period_start(), amount=1,
    )
    fake_redis.values[f"sim:active:{owner.pk}"] = 1
    first = SimulationRun.objects.get(pk=run.pk)
    second = SimulationRun.objects.get(pk=run.pk)

    tasks._fail(first, "chunk 0: boom")
    tasks._fail(second, "chunk 1: boom")

    assert UsageRecord.objects.get(
        user=owner, metric=UsageRecord.Metric.RUNS_STARTED
    ).amount == 0
    assert fake_redis.values[f"sim:active:{owner.pk}"] == 0


def test_deleting_a_deck_mid_run_gives_the_slot_back(run, fake_redis, owner, deck):
    """Otherwise the slot waited out its three-hour TTL, with nothing running."""
    fake_redis.values[f"sim:active:{owner.pk}"] = 1
    SimulationRun.objects.filter(pk=run.pk).update(status=SimulationRun.Status.RUNNING)

    deck.delete()

    assert fake_redis.values[f"sim:active:{owner.pk}"] == 0


def test_deleting_a_finished_run_leaves_the_slots_alone(run, fake_redis, owner, deck):
    fake_redis.values[f"sim:active:{owner.pk}"] = 1  # another run's slot
    SimulationRun.objects.filter(pk=run.pk).update(status=SimulationRun.Status.DONE)

    deck.delete()

    assert fake_redis.values[f"sim:active:{owner.pk}"] == 1


def test_dispatching_plans_the_chunks(run, monkeypatch):
    """`run_simulation` plans and hands off; it simulates nothing itself."""
    dispatched = {}

    def fake_chord(header):
        dispatched["header"] = header
        return lambda callback: dispatched.setdefault("callback", callback)

    monkeypatch.setattr(tasks, "chord", fake_chord)
    tasks.run_simulation(str(run.pk))
    run.refresh_from_db()

    assert run.status == SimulationRun.Status.RUNNING
    assert run.chunks_total == len(dispatched["header"]) >= 1
    assert run.started_at is not None
    assert "callback" in dispatched


def test_the_chunks_go_to_the_same_queue_as_the_run(owner, deck, monkeypatch):
    """The fairness mechanism, asserted where it actually failed.

    Routing only the dispatcher looks right and is not: the chunks then go to
    the DEFAULT queue, which is the short one, so a long run puts hundreds of
    tasks in front of every small one. The docker end-to-end check caught this
    by showing the long worker registering the task and never executing it.
    """
    long_run = SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=50_000, turns=3, seed=1
    )
    captured = {}

    def fake_chord(header):
        captured["header"] = header
        return lambda callback: captured.setdefault("callback", callback)

    monkeypatch.setattr(tasks, "chord", fake_chord)
    tasks.run_simulation(str(long_run.pk))

    assert all(
        signature.options["queue"] == services.LONG_QUEUE
        for signature in captured["header"]
    )
    assert captured["callback"].options["queue"] == services.LONG_QUEUE


def test_a_short_runs_chunks_stay_on_the_short_queue(run, monkeypatch):
    captured = {}

    def fake_chord(header):
        captured["header"] = header
        return lambda callback: None

    monkeypatch.setattr(tasks, "chord", fake_chord)
    tasks.run_simulation(str(run.pk))

    assert all(
        signature.options["queue"] == services.SHORT_QUEUE
        for signature in captured["header"]
    )


def test_a_task_for_a_deleted_run_does_not_raise(deck, owner):
    """A run can be deleted while its chunks are still queued."""
    missing = "00000000-0000-0000-0000-000000000000"

    assert tasks.run_simulation(missing) == "missing"
    assert tasks.simulate_chunk(missing, 0, 10) is tasks.SKIPPED
    assert tasks.finalize_run([], missing) == "missing"


# --- the progress bar ------------------------------------------------------


def test_only_a_completed_run_reads_one_hundred_percent(owner, deck):
    """A full bar above the word "Cancelled" is a screen that contradicts itself.

    Found by looking at a screenshot: a run cancelled at 5,000 of 10,000 games
    showed a 100% bar, because every terminal state was being treated as
    complete. No view test would have seen it - the page answered HTTP 200 with
    the wrong picture.
    """
    half = {"owner": owner, "deck": deck, "games_total": 10_000,
            "games_done": 5_000, "turns": 3, "seed": 1}

    cancelled = SimulationRun(status=SimulationRun.Status.CANCELLED, **half)
    failed = SimulationRun(status=SimulationRun.Status.FAILED, **half)
    running = SimulationRun(status=SimulationRun.Status.RUNNING, **half)
    done = SimulationRun(
        status=SimulationRun.Status.DONE,
        **{**half, "games_done": 10_000},
    )

    assert cancelled.progress_pct == 50
    assert failed.progress_pct == 50
    assert running.progress_pct == 50
    assert done.progress_pct == 100


def test_a_running_run_never_shows_a_full_bar(owner, deck):
    """Rounding must not claim completion while the page is still polling."""
    almost = SimulationRun(
        owner=owner, deck=deck, games_total=10_000, games_done=9_999,
        turns=3, seed=1, status=SimulationRun.Status.RUNNING,
    )

    assert almost.progress_pct == 99


def test_a_run_with_nothing_to_do_does_not_divide_by_zero(owner, deck):
    empty = SimulationRun(owner=owner, deck=deck, games_total=0, turns=3, seed=1)

    assert empty.progress_pct == 0


# --- mana by colour --------------------------------------------------------
#
# The last piece of colour work carried over from Phase 2. The engine spent
# five colours but reported one, so a deck whose mana is the wrong colour read
# exactly like a deck whose mana is right.


def test_the_report_shows_only_the_colours_the_deck_made(run, fake_redis):
    chunks = [tasks.simulate_chunk(str(run.pk), 0, 20)]
    tasks.finalize_run(chunks, str(run.pk))
    run.refresh_from_db()

    built = report.build(run)
    letters = [letter for letter, _label in built["color_columns"]]

    # The reference deck is mono-black with colourless rocks. Four empty
    # columns would teach a reader to stop reading the table.
    assert "B" in letters
    assert "G" not in letters
    assert built["color_rows"][0].turn == 1


def test_every_colour_column_has_a_word_for_it():
    """A letter on screen with no label is a number nobody can check."""
    for color in runner.MANA_SOURCES:
        assert report.COLOR_NAMES[color]


def test_the_colour_totals_agree_with_the_headline_total(run, fake_redis):
    """The colours have to add up to the number in the other table.

    Two tables on one page disagreeing about the same turn is the kind of
    defect that destroys trust in every other number on it.
    """
    chunks = [tasks.simulate_chunk(str(run.pk), 0, 20)]
    tasks.finalize_run(chunks, str(run.pk))
    run.refresh_from_db()

    built = report.build(run)
    for row, turn_row in zip(built["color_rows"], built["turn_rows"], strict=True):
        assert sum(row.amounts) == pytest.approx(turn_row.mana_mean, abs=1e-9)
        assert row.total == pytest.approx(turn_row.mana_mean, abs=1e-9)


def test_a_result_stored_before_colour_existed_still_renders(run, fake_redis):
    """Old runs must stay readable, and must not invent numbers.

    A stored result is evidence of what was measured. Nothing measured the
    colours, so the honest answer is no table at all - not a table of zeroes.
    """
    chunks = [tasks.simulate_chunk(str(run.pk), 0, 20)]
    tasks.finalize_run(chunks, str(run.pk))
    run.refresh_from_db()

    stripped = dict(run.result)
    stripped["turn_stats"] = [
        {key: value for key, value in stats.items()
         if key not in runner.COLOR_FIELD.values()}
        for stats in stripped["turn_stats"]
    ]
    run.result = stripped

    built = report.build(run)

    assert built["color_columns"] == []
    assert built["color_rows"] == []
    assert built["turn_rows"], "the rest of the report still has to work"


# --- the screens -----------------------------------------------------------


def test_the_run_page_shows_progress_while_it_works(client, owner, run):
    client.force_login(owner)
    response = client.get(reverse("simulations:detail", args=[run.pk]))

    assert response.status_code == 200
    assert b"hx-trigger" in response.content


def test_the_fragment_stops_polling_once_the_run_is_done(client, owner, run, fake_redis):
    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))
    client.force_login(owner)

    response = client.get(reverse("simulations:progress", args=[run.pk]))

    assert response.status_code == 200
    assert b"hx-trigger" not in response.content


def test_a_finished_poll_asks_htmx_to_reload(client, owner, run, fake_redis):
    """The report appears by itself, with no JavaScript written by hand."""
    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))
    client.force_login(owner)

    response = client.get(
        reverse("simulations:progress", args=[run.pk]), headers={"HX-Request": "true"}
    )

    assert response.headers["HX-Refresh"] == "true"


def test_the_report_renders_for_a_finished_run(client, owner, run, fake_redis):
    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))
    client.force_login(owner)

    response = client.get(reverse("simulations:detail", args=[run.pk]))
    body = response.content.decode()

    assert response.status_code == 200
    # Phase 10 T5.5: the opening-hand table left the page (the comparison it
    # showed is still asserted above, against real games).
    assert "Opening hands" not in body
    # How much of the deck the engine read is shown beside every result, never
    # omitted - and since Phase 9 C it is the only score: the casting-priority
    # half ("calls nobody has made") is no longer put to anybody.
    assert "The engine read" in body
    assert "nobody has made" not in body
    assert "Somebody had decided" not in body


def test_another_users_run_is_a_404_not_a_permission_error(client, run):
    """A permission error would still confirm the run exists."""
    intruder = User.objects.create_user(email="someone@example.com", password=PASSWORD)
    client.force_login(intruder)

    for name in ("simulations:detail", "simulations:progress"):
        assert client.get(reverse(name, args=[run.pk])).status_code == 404
    assert client.post(reverse("simulations:cancel", args=[run.pk])).status_code == 404


def test_signing_out_hides_every_run(client, run):
    response = client.get(reverse("simulations:detail", args=[run.pk]))

    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_cancelling_from_the_page_sets_the_flag(client, owner, run, monkeypatch):
    monkeypatch.setattr(services, "request_cancel", lambda run: True)
    client.force_login(owner)

    response = client.post(reverse("simulations:cancel", args=[run.pk]))

    assert response.status_code == 302
    assert response.url == run.get_absolute_url()


def test_starting_a_run_from_the_deck_page(client, owner, deck, fake_redis, no_dispatch):
    client.force_login(owner)

    response = client.post(
        reverse("simulations:create", args=[deck.pk]),
        {"games": 1000, "turns": 3, "on_the_play": 1},
    )

    run = SimulationRun.objects.get()
    assert response.status_code == 302
    assert response.url == run.get_absolute_url()
    assert run.games_total == 1000
    assert run.turns == 3
    assert run.on_the_play is True


def test_a_nonsense_size_never_reaches_a_worker(client, owner, deck, fake_redis, no_dispatch):
    """The form is the friendly first line; it still has to actually refuse."""
    client.force_login(owner)

    response = client.post(
        reverse("simulations:create", args=[deck.pk]),
        {"games": 999_999_999, "turns": 3, "on_the_play": 1},
    )

    assert response.status_code == 302
    assert not SimulationRun.objects.exists()


def test_one_user_cannot_run_another_users_deck(client, deck):
    intruder = User.objects.create_user(email="someone@example.com", password=PASSWORD)
    client.force_login(intruder)

    response = client.post(
        reverse("simulations:create", args=[deck.pk]),
        {"games": 1000, "turns": 3, "on_the_play": 1},
    )

    assert response.status_code == 404
    assert not SimulationRun.objects.exists()


def test_the_run_page_draws_what_was_seen(client, owner, run, fake_redis):
    """Phase 9 E: the draw statistics, as charts, on every new run."""
    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))
    client.force_login(owner)

    body = client.get(reverse("simulations:detail", args=[run.pk])).content.decode()

    assert "What you drew" in body
    assert "<polyline" in body
    assert "seen-bars-2" in body
    assert "Run the deck again" not in body


def test_the_curve_opens_on_turn_one(client, owner, run, fake_redis):
    """Phase 10 T5.4: always turn 1, and Firefox may not restore the last pick."""
    chunks = [tasks.simulate_chunk(str(run.pk), 0, 20)]
    tasks.finalize_run(chunks, str(run.pk))
    client.force_login(owner)

    body = client.get(reverse("simulations:detail", args=[run.pk])).content.decode()
    radios = re.findall(r'<input type="radio"\s+name="seen-curve-turn"[^>]*>', body)

    assert len(radios) > 1
    assert [("checked" in radio) for radio in radios] == [True] + [False] * (len(radios) - 1)
    assert 'id="seen-turn-1"' in radios[0]
    assert all('autocomplete="off"' in radio for radio in radios)


def test_an_old_run_asks_to_be_run_again(client, owner, run, fake_redis):
    """A run from before the count is not a deck that never draws ramp."""
    chunks = [tasks.simulate_chunk(str(run.pk), 0, 20)]
    tasks.finalize_run(chunks, str(run.pk))
    run.refresh_from_db()
    run.result = {key: value for key, value in run.result.items() if key != "seen"}
    run.save(update_fields=["result"])
    client.force_login(owner)

    response = client.get(reverse("simulations:detail", args=[run.pk]))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Run the deck again" in body
    # The milestones are drawn as lines too since phase 10; "What you drew" is not.
    seen = body[body.index('id="seen"'):body.index('id="milestones"')]
    assert "<polyline" not in seen


# --- phase 10 C: the report, re-ordered ---------------------------------------

def _finished_page(client, owner, run, **user_fields):
    for field, value in user_fields.items():
        setattr(owner, field, value)
    owner.save()
    chunks = [tasks.simulate_chunk(str(run.pk), 0, 20)]
    tasks.finalize_run(chunks, str(run.pk))
    client.force_login(owner)
    return client.get(reverse("simulations:detail", args=[run.pk])).content.decode()


def test_the_report_opens_with_what_a_person_can_do(client, owner, run, fake_redis):
    body = _finished_page(client, owner, run)
    order = ['id="attention"', 'id="seen"', 'id="milestones"', ">Mulligans<",
             'id="advanced"']
    positions = [body.index(marker) for marker in order]

    assert positions == sorted(positions)
    assert "What was actually modelled" not in body


def test_advanced_is_closed_and_holds_the_mana_table_and_the_engine(client, owner, run,
                                                                      fake_redis):
    body = _finished_page(client, owner, run)
    advanced = body[body.index('<details id="advanced"'):]

    assert re.match(r'<details id="advanced"[^>]*>', advanced)
    assert " open" not in re.match(r"<details[^>]*>", advanced).group(0)
    assert "Mana and lands, turn by turn" in advanced
    assert f"seed {run.seed}" in advanced
    assert "Mana and lands, turn by turn" not in body[:body.index('id="advanced"')]


def test_only_a_guest_is_offered_to_keep_the_deck(client, owner, run, fake_redis):
    from guests.services import LIFETIME

    member = _finished_page(client, owner, run)
    owner.is_guest = True
    owner.save()
    guest = client.get(reverse("simulations:detail", args=[run.pk])).content.decode()

    assert "Keep this deck" not in member
    assert "Keep this deck" in guest
    assert reverse("guests:save") in guest
    assert f"forgotten after {int(LIFETIME.total_seconds() // 3600)} hours" in guest
    assert "compared" not in guest, "P2: there is no run comparison to promise"


def test_attention_names_the_open_cards(client, owner, run, fake_redis, monkeypatch):
    monkeypatch.setattr("simulations.review.open_questions", lambda deck: 3)

    body = _finished_page(client, owner, run)

    assert "! 3 cards need you" in body
    assert reverse("simulations:review", args=[run.deck.pk]) in body


def test_attention_after_an_answer_asks_for_a_new_run(client, owner, run, fake_redis,
                                                       monkeypatch):
    monkeypatch.setattr("simulations.review.open_questions", lambda deck: 0)
    monkeypatch.setattr("simulations.report.annotations_changed_since", lambda run: True)

    body = _finished_page(client, owner, run)

    assert "All answered" in body
    assert "Run the deck again" in body


def test_attention_with_nothing_to_do_says_so(client, owner, run, fake_redis, monkeypatch):
    monkeypatch.setattr("simulations.review.open_questions", lambda deck: 0)
    monkeypatch.setattr("simulations.report.annotations_changed_since", lambda run: False)

    body = _finished_page(client, owner, run)

    assert "Nothing needs you" in body
    assert "The engine read" in body


def test_see_every_card_is_gone_from_every_template():
    """K7: the run page links to the cards it could not read, not to all of them."""
    templates = Path(__file__).resolve().parent.parent / "templates"
    for template in templates.rglob("*.html"):
        assert "See every card of the deck" not in template.read_text(encoding="utf-8"), template


def test_a_count_reads_as_a_mean_and_a_spread():
    from simulations.report import _spreads

    # Three games that drew 1, 2 and 3 creatures: mean 2, sd sqrt(2/3).
    spreads = _spreads({"cards": [6], "squares": [14]}, games=3)

    assert spreads[0].mean == pytest.approx(2.0)
    assert spreads[0].sd == pytest.approx((2 / 3) ** 0.5)
    assert _spreads({"cards": [6]}, games=3)[0].sd is None


def test_nine_milestones_draw_eight_lines_and_keep_nine_rows():
    rows = [{"key": f"m{n}", "label": f"M{n}", "shares": [float(n)] * 3} for n in range(9)]

    chart = report.milestone_chart(rows, 3)

    assert len(chart.lines) == report.MAX_LINES
    assert "m0" not in {line.key for line in chart.lines}, "the rarest one is left out"
