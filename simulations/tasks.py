"""The Celery side of a simulation.

The shape is a chord: a group of chunks, then one callback that merges them.

    run_simulation(run_id)
        -> chord(group(simulate_chunk.s(...) for each chunk), finalize_run.s(run_id))

Three decisions are worth knowing before changing anything here.

**Progress is written to the database, not read from the broker.** Each chunk
bumps `games_done` with an `F()` expression, so two workers finishing at the
same moment cannot lose each other's increment, and a worker that dies loses
one chunk rather than the run's whole history.

**Cancellation is cooperative.** Nothing here ever calls `revoke(terminate=True)`:
a SIGKILL in the middle of a chunk leaves half-written state, and - because
task ids are reused after a worker restart - can land on a different task
entirely. A chunk checks the flag before it starts and returns nothing. The
documented worst case is one chunk.

**The quota is settled once.** It is debited at enqueue and refunded here if the
run fails or is cancelled. Never per chunk: partial-refund arithmetic across
concurrent chunks is a bug farm, and the number it corrupts is one a user pays
for.
"""

import logging
import time

from celery import chord, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db.models import F
from django.utils import timezone

from billing import quotas
from billing.models import UsageRecord
from combos import measure
from simulations import services
from simulations.engine import adapter, runner
from simulations.models import SimulationRun

logger = logging.getLogger(__name__)

#: Returned by a chunk that declined to run because the run was cancelled.
#: Distinct from an empty result so `finalize_run` can tell "cancelled before
#: it started" from "produced nothing".
SKIPPED = None


@shared_task(name="simulations.run_simulation")
def run_simulation(run_id: str) -> str:
    """Plan the chunks and dispatch them.

    Deliberately does no simulating itself. It plans, records the plan and
    hands off - so that a run which is cancelled a second after it starts has
    nothing in flight to unwind.
    """
    run = SimulationRun.objects.filter(pk=run_id).first()
    if run is None:
        logger.warning("run_simulation: %s no longer exists", run_id)
        return "missing"
    if run.is_finished:
        return run.status
    if run.cancel_requested:
        # Cancelled between pressing the button and a worker picking this up.
        # Dispatching anyway would work - every chunk would decline - but it
        # would put a hundred pointless tasks through the queue first.
        if _close(run, SimulationRun.Status.CANCELLED):
            quotas.refund(run.owner, UsageRecord.Metric.RUNS_STARTED)
        return SimulationRun.Status.CANCELLED

    plan = runner.chunk_plan(run.games_total, run.turns, services.known_rate(run))

    SimulationRun.objects.filter(pk=run.pk).update(
        status=SimulationRun.Status.RUNNING,
        chunks_total=len(plan),
        started_at=timezone.now(),
    )

    # Every chunk is routed explicitly. Routing only this dispatcher would send
    # the actual work to the DEFAULT queue - which is `sim_short` - so a
    # 100,000-game run would put two hundred chunks in front of every free
    # user's 1,000-game run, defeating the one mechanism that exists to stop
    # exactly that. Verified by watching which worker picks the chunks up.
    queue = services.queue_for(run.games_total)
    header = [
        simulate_chunk.s(str(run.pk), index, games).set(queue=queue)
        for index, games in enumerate(plan)
    ]
    # When a chunk raises, Celery does not call the chord callback at all - it
    # fails the callback and calls its errbacks instead (see
    # `celery.backends.base.on_chord_part_return`). Without `on_error` a run
    # whose engine hit a bug would sit at RUNNING for ever, with the user's
    # quota spent and the page polling a progress bar that never moves.
    callback = (
        finalize_run.s(str(run.pk))
        .set(queue=queue)
        .on_error(fail_run.s(str(run.pk)).set(queue=queue))
    )
    chord(header)(callback)
    return f"dispatched {len(plan)} chunks"


@shared_task(name="simulations.simulate_chunk")
def simulate_chunk(run_id: str, index: int, games: int):
    """Play one chunk of one run.

    The cancellation check costs one cheap query per chunk - roughly one query
    per fifteen seconds of work - which is what buys a cancel that does not
    have to kill anything.
    """
    run = SimulationRun.objects.filter(pk=run_id).select_related("deck").first()
    if run is None or run.cancel_requested or run.is_finished:
        return SKIPPED

    started = time.perf_counter()
    try:
        conversion = adapter.convert(run.deck)
        # Recomputed per chunk rather than carried from the dispatcher: only
        # JSON travels between tasks, and the plan holds engine objects. It is
        # deterministic, so every chunk watches the same combos.
        plan = measure.plan_for(run)
        samples = measure.samples_for(run, plan, index, run.chunks_total)
        payload = runner.run_chunk(
            games,
            run_seed=run.seed,
            index=index,
            turns=run.turns,
            on_the_play=run.on_the_play,
            deck=measure.with_key_cards(conversion.definition, plan),
            watch=plan.watches,
            samples=samples,
        )
    except Exception as exc:
        # Closed out here, by the worker that saw it, rather than left to the
        # chord's error handling. A run stuck at RUNNING with the quota spent
        # is the worst outcome available, and it is not worth betting against
        # the errback machinery firing to avoid it. `_close` is idempotent, so
        # an errback arriving afterwards changes nothing.
        logger.exception("chunk %s of run %s failed", index, run_id)
        _fail(run, f"chunk {index}: {exc}")
        raise
    elapsed = time.perf_counter() - started

    # F() rather than read-modify-write: chunks land concurrently, and a lost
    # update here would leave a progress bar that never reaches its own total.
    progress = {
        "games_done": F("games_done") + games,
        "chunks_done": F("chunks_done") + 1,
    }
    played = games + sum(sample.games for sample in samples)
    if index == 0 and played:
        # Only the first chunk records the rate. Every chunk writing it would
        # be needless contention for a number that barely varies between them.
        #
        # Divided by every game the chunk played, hypotheticals included. They
        # are the same work on the same core, and charging them to the run's
        # own games would record this deck as half again as slow as it is - a
        # figure that then sizes the chunks of the *next* run, which may have
        # no hypotheticals at all.
        progress["usec_per_game"] = elapsed * 1_000_000 / played
    SimulationRun.objects.filter(pk=run_id).update(**progress)
    return payload


@shared_task(name="simulations.finalize_run")
def finalize_run(chunks, run_id: str) -> str:
    """Merge the chunks and close the run out.

    The chord callback. `chunks` arrives in whatever order the workers
    finished, which is exactly why `runner.merge` adds rather than appends.
    """
    run = SimulationRun.objects.filter(pk=run_id).first()
    if run is None:
        logger.warning("finalize_run: %s no longer exists", run_id)
        return "missing"
    if run.is_finished:
        # A chunk already closed this out by failing. Refunding again here
        # would hand back quota the user never spent.
        return run.status

    produced = [chunk for chunk in (chunks or []) if chunk]

    if run.cancel_requested or not produced:
        if _close(run, SimulationRun.Status.CANCELLED):
            quotas.refund(run.owner, UsageRecord.Metric.RUNS_STARTED)
        return SimulationRun.Status.CANCELLED

    try:
        merged = runner.merge(produced)
    except ValueError as exc:
        # A merge that refuses is a real disagreement between chunks, not a
        # transient failure: retrying would produce the same refusal.
        logger.exception("finalize_run: %s could not be merged", run_id)
        if _close(run, SimulationRun.Status.FAILED, error=str(exc)):
            quotas.refund(run.owner, UsageRecord.Metric.RUNS_STARTED)
        return SimulationRun.Status.FAILED

    conversion = adapter.convert(run.deck)
    run.result = merged
    run.gaps = [
        {"card": gap.card, "field": gap.field, "reason": gap.reason}
        for gap in conversion.gaps
    ]
    run.cards_total = conversion.cards_total
    run.cards_with_gaps = conversion.cards_with_gaps
    run.copies_total = conversion.copies_total
    run.copies_unreadable = conversion.copies_unreadable
    run.engine_version = conversion.engine_version
    run.library_size = conversion.definition.size
    run.lands_total = conversion.definition.land_count
    run.games_done = merged["iterations"]
    if not _close(run, SimulationRun.Status.DONE, fields=[
        "result", "gaps", "cards_total", "cards_with_gaps",
        "copies_total", "copies_unreadable",
        "engine_version", "library_size", "lands_total", "games_done",
    ]):
        return SimulationRun.objects.get(pk=run.pk).status
    # The hypothetical decks played real games on a real worker. Counting only
    # the run's own iterations would be this application under-reporting its
    # costs to itself - and the usage record is what the next plan limit will
    # be argued from.
    extra = _store_measurements(run, merged)
    quotas.consume(run.owner, UsageRecord.Metric.GAMES_SIMULATED,
                   merged["iterations"] + extra)
    return SimulationRun.Status.DONE


def _store_measurements(run: SimulationRun, merged: dict) -> int:
    """Keep what the run measured about combos, and never fail a run over it.

    A finished run whose numbers are good is worth more than a combo
    percentage. If the lookup moved underneath the run, or a combo stopped
    resolving, that is a missing row on a panel - not a reason to throw away
    ten thousand games somebody paid for.
    """
    try:
        return measure.store(run, merged.get("combos"))
    except Exception:
        logger.exception("combo measurements for run %s could not be stored", run.pk)
        return 0


@shared_task(name="simulations.fail_run")
def fail_run(run_id: str, request=None, exc=None, traceback=None) -> str:
    """Close out a run whose chunks raised.

    A new-style errback, so Celery calls it with the failed request, the
    exception and the traceback appended to the run id it was built with. It
    runs in the process that noticed the failure, so it stays short and never
    raises: the one thing worse than a failed run is a failed run that also
    loses the user's quota.
    """
    run = SimulationRun.objects.filter(pk=run_id).first()
    if run is None or run.is_finished:
        return "already closed"

    logger.error("simulation %s failed: %r", run_id, exc)
    _fail(run, str(exc) if exc else "a chunk failed")
    return SimulationRun.Status.FAILED


def _fail(run: SimulationRun, reason: str) -> None:
    """Mark a run failed and give the quota back, at most once."""
    if run.is_finished:
        return
    if _close(run, SimulationRun.Status.FAILED, error=reason):
        quotas.refund(run.owner, UsageRecord.Metric.RUNS_STARTED)


def _close(run: SimulationRun, status: str, *, error: str = "", fields=None) -> bool:
    """Write the terminal state, free the concurrency slot, and say who won.

    Every terminal path goes through here, which is what keeps the slot from
    leaking: a run that ends in any way at all gives its slot back.

    **At most once, decided by the database.** The write is conditional - it
    only touches a row that is not already finished - and the caller refunds
    the quota only when this returns True. Until the 2026-09-25 review each
    worker checked `run.is_finished` on its own copy of the row, loaded when
    its chunk started: a deck that broke the engine broke every chunk, two
    workers failed at the same moment, both copies still read RUNNING, and the
    run was refunded and its slot released twice. The second release could
    free a slot another run was still holding.

    The slot is released after the write now, because only the write knows
    whether this call is the one that closed the run. That opens a window of a
    few milliseconds in which a finished run still holds its slot, which is
    the harmless direction to be wrong in.
    """
    now = timezone.now()
    values = {name: getattr(run, name) for name in (fields or [])}
    values.update(status=status, error=error, finished_at=now)
    closed = (
        SimulationRun.objects.filter(pk=run.pk)
        .exclude(status__in=SimulationRun.TERMINAL)
        .update(**values)
    )
    if not closed:
        return False
    services.finish_slot(run)
    run.status, run.error, run.finished_at = status, error, now
    return True


# --- the deck summary (phase 10 H) ---------------------------------------------


@shared_task(name="simulations.close_stale_summaries")
def close_stale_summaries() -> int:
    """P1: every ten minutes, close summaries whose worker died mid-call."""
    from simulations import summary

    return summary.close_stale()


@shared_task(name="simulations.write_summary")
def write_summary(summary_id: str) -> str:
    """Have Mistral write a deck's summary, check it, and keep it.

    On the default queue, beside the run it was started with, and short: one
    request. Any failure - Mistral down, an answer that does not check out -
    closes the row as failed and gives back the run it was charged, **once**:
    the row is closed by a conditional update, and only the call that closed
    it refunds, the way `_close` settles a run.
    """
    from simulations import mistral, summary
    from simulations.models import DeckSummary

    row = DeckSummary.objects.select_related("deck", "deck__owner").filter(
        pk=summary_id, status=DeckSummary.Status.PENDING).first()
    if row is None:
        return "nothing to write"
    deck = row.deck
    try:
        readings = adapter.readings(deck)
        deck_facts = summary.facts(deck, readings)
        completion = mistral.complete(summary.messages(deck_facts, row.language),
                                      max_tokens=summary.MAX_TOKENS)
    except (mistral.MistralError, SoftTimeLimitExceeded) as exc:
        # No answer, so nothing billed: today's budget gets its slot back.
        return summary.fail(row, f"{type(exc).__name__}: {exc}", answered=False)
    try:
        content = summary.parse(completion.content, deck_facts["strategies"])
    except ValueError as exc:
        # An answer that does not check out was billed all the same.
        return summary.fail(row, str(exc), answered=True)

    DeckSummary.objects.filter(pk=row.pk, status=DeckSummary.Status.PENDING).update(
        status=DeckSummary.Status.DONE,
        content=content,
        model_name=completion.model[:64],
        prompt_version=summary.PROMPT_VERSION,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        updated_at=timezone.now(),
    )
    return DeckSummary.Status.DONE
