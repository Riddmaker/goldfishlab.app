"""Starting and stopping simulations, and recording judgements about cards.

Everything a view needs in order to run a deck lives here, so that the view
stays a view. Four things happen at enqueue and the order matters:

1. the request is checked against the user's **plan limits** (games, turns);
2. the **monthly quota** is checked and debited - once, here, never per chunk;
3. a **concurrency slot** is taken, so one user cannot occupy every worker;
4. the run row is written and the task dispatched to the right queue.

If step 3 fails the quota is given straight back, because nothing was run.

The second half of the module, from "recording a judgement" on, is Phase 4's
annotation writing. It is here for the same reason: four decisions have to be
got right every time an annotation is saved, and a view is not the place to
keep remembering them.
"""

import secrets

from django.conf import settings
from django.db import transaction
from redis import Redis
from redis.exceptions import RedisError

from billing import quotas
from billing.models import UsageRecord
from simulations.models import SimulationRun

#: Runs at or below this size go to the short queue, which has a worker of its
#: own. This is the real fairness mechanism: without it a single 100,000 game
#: run blocks every free user's 1,000 game run for minutes, and no quota
#: system fixes that - the quota is spent either way, the user just waits.
SHORT_QUEUE_MAX_GAMES = 10_000
SHORT_QUEUE = "sim_short"
LONG_QUEUE = "sim_long"

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
    games = _within(games, plan.max_games_per_run, "games")
    turns = _within(turns, plan.max_turns, "turns")

    quotas.check(owner, UsageRecord.Metric.RUNS_STARTED)

    if not _take_slot(owner, plan.max_concurrent_runs):
        raise TooManyRuns(
            f"You already have {plan.max_concurrent_runs} simulation(s) running. "
            "Wait for one to finish, or cancel it."
        )

    try:
        with transaction.atomic():
            quotas.consume(owner, UsageRecord.Metric.RUNS_STARTED)
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


def _dispatch(run: SimulationRun, tasks) -> None:
    async_result = tasks.run_simulation.apply_async(
        args=[str(run.pk)], queue=queue_for(run.games_total)
    )
    SimulationRun.objects.filter(pk=run.pk).update(task_id=async_result.id)


def queue_for(games: int) -> str:
    """Which queue a run of this size belongs on."""
    return SHORT_QUEUE if games <= SHORT_QUEUE_MAX_GAMES else LONG_QUEUE


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


def _within(value: int, limit: int | None, what: str) -> int:
    value = int(value)
    if value < 1:
        raise SimulationRefused(f"A run needs at least one {what[:-1]}.")
    if limit is not None and value > limit:
        raise SimulationRefused(
            f"Your plan allows at most {limit:,} {what} per run; you asked for "
            f"{value:,}."
        )
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
        raise SimulationRefused(
            "Simulations are unavailable at the moment. Please try again shortly."
        ) from None
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
