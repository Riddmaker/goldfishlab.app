"""Starting and stopping simulations, and recording judgements about cards.

Everything a view needs in order to run a deck lives here, so that the view
stays a view. Four things happen at enqueue and the order matters:

1. the request is checked against the user's **plan limits** (games, turns);
2. the **monthly quota** is checked and debited - once, here, never per chunk;
3. a **concurrency slot** is taken, so one user cannot occupy every worker;
4. the run row is written and the task dispatched to the right queue.

If step 3 fails the quota is given straight back, because nothing was run.

Since phase 10 H a run can bring the deck's written summary with it, when the
deck changed since the last one (`simulations.summary.due`). That costs one
more run of the allowance: two are checked and debited together. With only
one left the run has priority and goes alone - the page then says there was
no run left for a new summary. A guest's one summary is free.

The second half of the module, from "recording a judgement" on, is Phase 4's
annotation writing. It is here for the same reason: four decisions have to be
got right every time an annotation is saved, and a view is not the place to
keep remembering them.
"""

import secrets
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext, ngettext
from redis import Redis
from redis.exceptions import RedisError

from billing import quotas
from billing.models import UsageRecord
from core.l10n import number
from metrics import counts
from simulations.engine import runner
from simulations.models import SimulationRun

#: Runs at or below this size go to the short queue, which has a worker of its
#: own. This is the real fairness mechanism: without it a single 100,000 game
#: run blocks every free user's 1,000 game run for minutes, and no quota
#: system fixes that - the quota is spent either way, the user just waits.
SHORT_QUEUE_MAX_GAMES = 10_000
SHORT_QUEUE = "sim_short"
LONG_QUEUE = "sim_long"

#: How many chunks of one run play side by side: the short queue has a worker
#: process of its own, the long queue two (docker-compose.yml; production, too,
#: has a worker node per queue, worker-short and worker - the manifest outside
#: this repository). Only the pace of the progress bar and the queue's wait
#: read this, so erring on the slow side costs nothing.
PARALLEL_CHUNKS = {SHORT_QUEUE: 1, LONG_QUEUE: 2}
#: From pressing the button to the first chunk playing, on an empty queue: the
#: dispatcher task and the broker round trips.
QUEUE_SECONDS = 2.0

#: How long a concurrency slot lives if nothing ever releases it. A worker
#: killed mid-run would otherwise hold a slot for ever, and the user would be
#: locked out of their own account's simulations with no way to tell why.
SLOT_TTL_SECONDS = 3 * 60 * 60


class SimulationRefused(Exception):
    """The run was not started, with a sentence saying why."""


class TooManyRuns(SimulationRefused):
    """The user already has as many runs in flight as their plan allows."""


def start_run(*, owner, deck, games: int, turns: int, on_the_play: bool = True,
              seed: int | None = None) -> SimulationRun:
    """Validate, debit, and dispatch one simulation.

    Raises:
        SimulationRefused: The request exceeds the plan's per-run limits.
        TooManyRuns: The user is already at their concurrency limit.
        billing.quotas.QuotaExceeded: The monthly run quota is used up.
    """
    plan = quotas.plan_for(owner)
    games = _within(games, plan.max_games_per_run, GAMES)
    turns = _within(turns, plan.max_turns, TURNS)

    quotas.check(owner, UsageRecord.Metric.RUNS_STARTED)

    # The summary rides along when it is due and, for a member, when the
    # allowance has room for both (phase 10 H, T6.5). Decided before the slot
    # is taken, so nothing here can leak one.
    from simulations import summary

    write_summary = summary.due(owner, deck)
    charge_summary = write_summary and not owner.is_guest
    if charge_summary:
        both = quotas.check(owner, UsageRecord.Metric.RUNS_STARTED, amount=2,
                            raise_on_fail=False)
        write_summary = charge_summary = both.allowed

    # Guests share one allowance of workers between them, whoever they are.
    if getattr(owner, "is_guest", False):
        from guests import services as guests

        if guests.busy():
            raise SimulationRefused(gettext(
                "A lot of people are trying Goldfish Lab right now. Try again in "
                "a minute - or save your deck with a free account, which does "
                "not wait for guests."
            ))

    if not _take_slot(owner, plan.max_concurrent_runs):
        raise TooManyRuns(ngettext(
            "You already have %(count)s simulation running. Wait for it to finish, "
            "or cancel it.",
            "You already have %(count)s simulations running. Wait for one to finish, "
            "or cancel it.",
            plan.max_concurrent_runs,
        ) % {"count": plan.max_concurrent_runs})

    try:
        with transaction.atomic():
            quotas.consume(owner, UsageRecord.Metric.RUNS_STARTED)
            budget_day = write_summary and summary.claim(owner, deck)
            if budget_day:
                if charge_summary:
                    quotas.consume(owner, UsageRecord.Metric.RUNS_STARTED)
                summary.begin(deck, charged=charge_summary, budget_day=budget_day)
            # P2. In the same transaction, so a run that is not made is not counted.
            counts.run_started(owner)
            run = SimulationRun.objects.create(
                owner=owner,
                deck=deck,
                games_total=games,
                turns=turns,
                on_the_play=on_the_play,
                # Not `random`: a seed needs to be unpredictable only so that
                # two runs started in the same second differ, and `secrets`
                # says that without the engine's reproducibility caveat.
                seed=seed if seed is not None else secrets.randbelow(2**62),
                deck_print=summary.fingerprint(deck),
            )
    except Exception:
        _release_slot(owner)
        raise

    # After the commit, never inside it: a worker can pick the task up before
    # an open transaction commits, and would then look for a row that is not
    # there yet.
    from simulations import tasks

    transaction.on_commit(
        lambda: _dispatch(run, tasks)
    )
    return run


def start_lab_run(*, owner, deck, games: int, turns: int, on_the_play: bool,
                  seed: int) -> SimulationRun:
    """A run for the site's own pages (P11, `datapages`), owned by the system
    account: no plan limits, quota, slot, summary or count, because nobody
    asked for it. It goes on the long queue (`queue_of`), so a guest never
    waits behind a precon."""
    if not owner.is_system:
        raise ValueError("start_lab_run is for the system account only")
    from simulations import summary, tasks

    run = SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=games, turns=turns,
        on_the_play=on_the_play, seed=seed, deck_print=summary.fingerprint(deck),
    )
    transaction.on_commit(lambda: _dispatch(run, tasks))
    return run


def _dispatch(run: SimulationRun, tasks) -> None:
    async_result = tasks.run_simulation.apply_async(
        args=[str(run.pk)], queue=queue_of(run)
    )
    SimulationRun.objects.filter(pk=run.pk).update(task_id=async_result.id)


def queue_of(run: SimulationRun) -> str:
    """The queue this run and its chunks go on: the system account's on the
    long one whatever their size (P11), everybody else's by size."""
    return LONG_QUEUE if run.owner.is_system else queue_for(run.games_total)


def queue_for(games: int) -> str:
    """Which queue a run of this size belongs on."""
    return SHORT_QUEUE if games <= SHORT_QUEUE_MAX_GAMES else LONG_QUEUE


def known_rate(run: SimulationRun) -> float | None:
    """How fast this deck actually simulated last time, if it ever has.

    Better than any estimate: the same deck, the same engine, the same
    hardware. Only runs with the same turn count qualify, because the cost of a
    game is linear in the number of turns it plays.
    """
    return (
        SimulationRun.objects.filter(
            deck_id=run.deck_id,
            turns=run.turns,
            usec_per_game__isnull=False,
        )
        .exclude(pk=run.pk)
        .order_by("-created_at")
        .values_list("usec_per_game", flat=True)
        .first()
    )


def expected_seconds(run: SimulationRun) -> float:
    """About how long a run takes, queue included (phase 10 T3.1).

    Sets the pace of the progress bar between two real batches, nothing else:
    the bar never shows less than the real share, so a wrong guess only makes
    it glide faster or slower. The rate is this run's own once its first chunk
    has measured it, else this deck's last one, else the engine's estimate.
    """
    rate = run.usec_per_game or known_rate(run) or runner.default_usec_per_game(run.turns)
    games = max(1, run.games_total)
    chunks = run.chunks_total or len(runner.chunk_plan(games, run.turns, rate))
    parallel = min(PARALLEL_CHUNKS[queue_for(games)], chunks)
    return QUEUE_SECONDS + games * rate / 1_000_000 / parallel


@dataclass(frozen=True)
class Queue:
    """Where a waiting run stands (P1): the runs before it on its queue."""

    ahead: int
    seconds: float


def queue_ahead(run: SimulationRun) -> Queue:
    """The runs on this run's queue that a worker takes before it, and about
    how long they keep the workers busy (P1).

    `expected_seconds` assumed an empty queue, so a crowd saw "waiting for a
    free table" with no idea for how long. The guess uses the engine's own
    estimate per game - not each run's measured rate, which would cost a
    query a run on every poll.
    """
    short = queue_for(run.games_total) == SHORT_QUEUE
    size = ({"games_total__lte": SHORT_QUEUE_MAX_GAMES} if short
            else {"games_total__gt": SHORT_QUEUE_MAX_GAMES})
    before = SimulationRun.objects.filter(
        Q(status=SimulationRun.Status.RUNNING)
        | Q(status=SimulationRun.Status.PENDING, created_at__lt=run.created_at),
        **size,
    ).exclude(pk=run.pk).values_list("games_total", "games_done", "turns")
    ahead = 0
    usec = 0.0
    for total, done, turns in before:
        ahead += 1
        usec += max(0, total - done) * runner.default_usec_per_game(turns)
    parallel = PARALLEL_CHUNKS[SHORT_QUEUE if short else LONG_QUEUE]
    return Queue(ahead=ahead, seconds=usec / 1_000_000 / parallel)


def request_cancel(run: SimulationRun) -> bool:
    """Ask a run to stop. Returns False if it had already finished.

    Cooperative, and **only** cooperative: this sets a flag. Every chunk reads
    it before starting and returns nothing, the chord completes with nothing in
    it, and `finalize_run` closes the run out as cancelled and refunds the
    quota. The documented worst case is one chunk - about fifteen seconds.

    It deliberately does not `revoke()` anything, which is not the obvious
    choice and was not the first one. Revoking the queued chunks looks like a
    cheap optimisation and breaks the run: a revoked header task makes the
    whole chord raise `TaskRevokedError` instead of calling its callback, so
    nothing ever closes the run out and it sits at RUNNING for ever with the
    user's quota spent. Observed against real workers, not theorised - a
    cancelled 10,000-game run stayed RUNNING for two minutes and simulated
    every game anyway.

    `revoke(terminate=True)` is doubly out: a SIGKILL into a worker that has
    restarted since can land on somebody else's task entirely.
    """
    if run.is_finished:
        return False

    SimulationRun.objects.filter(pk=run.pk).update(cancel_requested=True)
    return True


def finish_slot(run: SimulationRun) -> None:
    """Give back the concurrency slot a finished run was holding."""
    _release_slot(run.owner)


# --- plan limits -----------------------------------------------------------


GAMES = "games"
TURNS = "turns"


def _within(value: int, limit: int | None, what: str) -> int:
    value = int(value)
    numbers = {"limit": number(limit or 0), "value": number(value)}
    if value < 1:
        raise SimulationRefused(gettext("A run needs at least one game.") if what == GAMES
                                else gettext("A run needs at least one turn."))
    if limit is not None and value > limit:
        raise SimulationRefused((
            ngettext("Your plan allows at most %(limit)s game per run; you asked for "
                     "%(value)s.",
                     "Your plan allows at most %(limit)s games per run; you asked for "
                     "%(value)s.", limit)
            if what == GAMES else
            ngettext("Your plan allows at most %(limit)s turn per run; you asked for "
                     "%(value)s.",
                     "Your plan allows at most %(limit)s turns per run; you asked for "
                     "%(value)s.", limit)
        ) % numbers)
    return value


# --- concurrency -----------------------------------------------------------
#
# A counter in Redis rather than a count of rows, because two enqueues arriving
# together would both read "0 running" and both start. INCR is atomic, so
# exactly one of them sees the number that puts it over the limit.


def _redis() -> Redis:
    return Redis.from_url(settings.CELERY_BROKER_URL)


def _slot_key(owner) -> str:
    return f"sim:active:{owner.pk}"


def _take_slot(owner, limit: int | None) -> bool:
    """Claim one of the user's concurrent-run slots."""
    if not limit:
        return True
    try:
        client = _redis()
        active = client.incr(_slot_key(owner))
        client.expire(_slot_key(owner), SLOT_TTL_SECONDS)
    except RedisError:
        # Redis is the broker: if it is down, nothing can run anyway. Refusing
        # here rather than starting a run that will never be picked up.
        raise SimulationRefused(gettext(
            "Simulations are unavailable at the moment. Please try again shortly."
        )) from None
    if active > limit:
        _release_slot(owner)
        return False
    return True


def _release_slot(owner) -> None:
    """Give a slot back, never letting the counter go negative.

    A negative counter would hand the user unlimited concurrency, which is the
    opposite of what this exists for - so the floor is enforced rather than
    assumed.
    """
    try:
        client = _redis()
        if client.decr(_slot_key(owner)) < 0:
            client.set(_slot_key(owner), 0, ex=SLOT_TTL_SECONDS)
    except RedisError:
        # Nothing to do about it here; the TTL is what makes this self-healing.
        pass


# --- recording a judgement --------------------------------------------------
#
# Writing an annotation is three lines of ORM and four decisions, which is why
# it lives here rather than in the view:
#
# 1. **Which row.** The unique constraint is `(owner, deck, oracle_card)` with
#    `nulls_distinct=False`, so the scope has to be turned into an exact
#    triple and matched on it - otherwise a second save raises IntegrityError
#    instead of editing what is already there.
# 2. **Merge, never replace.** `annotations.apply` keeps the keys the editor
#    does not show. Cabal Coffers' row carries `scaling_rule`,
#    `scaling_subtype` and `scaling_activation`; someone editing its priority
#    must not silently delete the per-Swamp scaling that makes it that card.
# 3. **An empty judgement is no judgement.** A row with no overrides and no
#    note is deleted rather than stored, so that "I changed my mind" leaves
#    nothing behind for the provenance panel to credit the user with.
# 4. **Never `owner=None`.** That scope applies to every deck of every user
#    and no user-facing path may write it.


def annotation_at(deck, oracle_card, scope: str):
    """The user's own annotation row for this card at this scope, if any."""
    from simulations.models import CardAnnotation

    return CardAnnotation.objects.filter(
        oracle_card=oracle_card, **_scope_keys(deck, scope)
    ).first()


def save_annotation(*, deck, oracle_card, scope: str, judgements: dict,
                    note: str = ""):
    """Write one judgement, merged into whatever is already stored.

    A "Looks right" already on the row is kept, and keeps the row: a row
    carrying only that flag is an answer, not an empty judgement.

    Returns:
        CardAnnotation | None: The row, or `None` when the judgement was empty
        and the row was removed.

    Raises:
        ValueError: On a scope no user may write.
        django.core.exceptions.ValidationError: From the model's own `clean`,
            which is what rejects a colour the engine would not recognise.
    """
    from simulations.annotations import apply
    from simulations.models import CardAnnotation

    keys = _scope_keys(deck, scope)
    existing = CardAnnotation.objects.filter(oracle_card=oracle_card, **keys).first()
    overrides = apply(existing.overrides if existing else {}, judgements)
    note = (note or "").strip()
    confirmed = bool(existing and existing.confirmed)
    _recount_holding(deck, oracle_card, scope)

    if not overrides and not note and not confirmed:
        if existing:
            existing.delete()
        return None

    annotation, _created = CardAnnotation.objects.update_or_create(
        oracle_card=oracle_card,
        defaults={"overrides": overrides, "note": note, "confirmed": confirmed},
        **keys,
    )
    return annotation


def confirm_annotation(*, deck, oracle_card, scope: str):
    """Record "Looks right": the owner read the engine's reading and agrees.

    Nothing the engine reads changes - the stored values stay as they were,
    which is why this does not go through `save_annotation`: an empty form
    handed to `annotations.apply` would remove every judgement on the card.
    """
    from simulations.models import CardAnnotation

    annotation, _created = CardAnnotation.objects.update_or_create(
        oracle_card=oracle_card, defaults={"confirmed": True},
        **_scope_keys(deck, scope),
    )
    _recount_holding(deck, oracle_card, scope)
    return annotation


def delete_annotation(*, deck, oracle_card, scope: str) -> bool:
    """Remove a judgement entirely, falling back to the derived reading.

    Returns:
        bool: Whether there was anything to remove.
    """
    from simulations.models import CardAnnotation

    deleted, _ = CardAnnotation.objects.filter(
        oracle_card=oracle_card, **_scope_keys(deck, scope)
    ).delete()
    if deleted:
        _recount_holding(deck, oracle_card, scope)
    return bool(deleted)


def _recount_holding(deck, oracle_card, scope: str) -> None:
    """Count the red marker again on every deck this judgement reaches.

    One deck for a deck-scoped row; for one about all the owner's decks, each
    of theirs that holds the card - in the 99 or in the command zone.
    """
    from django.db.models import Q

    from decks.models import Deck
    from decks.services import recount_later
    from simulations.engine.adapter import USER_SCOPE

    if scope == USER_SCOPE:
        reached = Deck.objects.filter(owner=deck.owner).filter(
            Q(entries__oracle_card=oracle_card) | Q(commander=oracle_card)
        ).distinct()
    else:
        reached = Deck.objects.filter(pk=deck.pk)
    recount_later(Deck.objects.filter(pk__in=reached.values("pk")))


def _scope_keys(deck, scope: str) -> dict:
    """The exact `(owner, deck)` pair one scope means.

    `builtin` is absent on purpose: a built-in annotation applies to every deck
    of every user, and nothing a user can reach may write one. A typo'd scope
    has to raise here rather than fall through to a broader scope than the one
    that was asked for.
    """
    from simulations.engine.adapter import DECK_SCOPE, USER_SCOPE

    if scope == DECK_SCOPE:
        return {"owner": deck.owner, "deck": deck}
    if scope == USER_SCOPE:
        return {"owner": deck.owner, "deck": None}
    raise ValueError(
        f"{scope!r} is not a scope a user may write; "
        f"expected {DECK_SCOPE!r} or {USER_SCOPE!r}"
    )
