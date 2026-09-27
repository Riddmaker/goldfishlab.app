"""Quota enforcement.

There are exactly FOUR call sites for `check()` in the finished application:

  1. simulation enqueue   (Phase 3)
  2. deck create          (Phase 1)
  3. deck/collection import (Phase 1)
  4. API token issue      (Phase 6)

Quota logic anywhere else is a bug. Keeping the count of call sites small is
what makes the limits auditable.

Accounting rule: quota is debited at ENQUEUE and refunded on cancel or
failure. Never debit per chunk - partial-refund arithmetic is a bug farm.

`consume()` is a different thing from `check()` and is deliberately not on that
list. It records what happened; `check()` decides what may happen. Phase 7 §2 is
the case that makes the distinction worth stating: a simulation run also plays
games on *hypothetical* decks, to price the card a combo is missing, and those
games are added to `GAMES_SIMULATED` where the run's own games are recorded.
They are metered, in the sense of being counted honestly. They are not gated,
because the run they ride on already was.
"""

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from billing.models import Plan, Subscription, UsageRecord


class QuotaExceeded(Exception):
    """Raised when an action would exceed the user's plan limits."""

    def __init__(self, message: str, *, metric: str, limit: int, used: int):
        super().__init__(message)
        self.metric = metric
        self.limit = limit
        self.used = used


@dataclass(frozen=True)
class QuotaDecision:
    """The outcome of a quota check."""

    allowed: bool
    metric: str
    limit: int | None
    used: int
    remaining: int | None

    @property
    def unlimited(self) -> bool:
        return self.limit is None


def period_start(when: date | None = None) -> date:
    """First day of the billing month."""
    # The application's own time zone, not the container's: `date.today()`
    # is the host clock, which in the production image is UTC, so a month
    # would have turned over an hour or two away from midnight in Zurich.
    today = when or timezone.localdate()
    return today.replace(day=1)


def plan_for(user) -> Plan:
    """The user's current plan, falling back to the default free plan.

    Two fallbacks, and the second one is a rule rather than robustness:

    * **No subscription row** - cannot happen, since every user is given one at
      signup. Belt to that braces.
    * **A cancelled subscription does not entitle anybody to anything.** The
      webhook sets `plan` back to free on `customer.subscription.deleted`, so
      this is a second lock on the same door; if the two ever disagree, the
      cautious answer is the one that does not hand out a paid plan.

    `past_due` deliberately keeps the plan. Stripe retries a failed card for
    about two weeks and the person has not cancelled anything - what the
    billing phase forbids is leaving them there *silently*, which
    `Subscription.needs_attention` answers with a banner.
    """
    try:
        subscription = user.subscription
    except Subscription.DoesNotExist:
        return Plan.objects.get(is_default=True)

    if subscription.status == Subscription.Status.CANCELED:
        return Plan.objects.get(is_default=True)
    return subscription.plan


#: A limit counted from the live tables rather than from `UsageRecord`.
#:
#: `max_decks` used to be checked against `DECKS_CREATED`, a monthly counter:
#: deleting a deck gave no slot back, and on the first of the month a Free
#: account could make three more, so the "Decks: 3" on the plans page was
#: neither a ceiling nor a monthly allowance anybody would guess. Settled with
#: the user on 2026-09-25: the limit is decks *owned*. `DECKS_CREATED` is still
#: recorded, as history.
DECKS_OWNED = "decks_owned"

_LIVE_COUNTS = {
    DECKS_OWNED: lambda user: user.decks.count(),
}


def used(user, metric: str, when: date | None = None) -> int:
    """How much of `metric` the user has consumed this period.

    For a live count - `DECKS_OWNED` - there is no period: it is what the
    person has right now.
    """
    if metric in _LIVE_COUNTS:
        return _LIVE_COUNTS[metric](user)
    record = UsageRecord.objects.filter(
        user=user, metric=metric, period_start=period_start(when)
    ).first()
    return record.amount if record else 0


#: The whole of the mapping from "what somebody did" to "what their plan
#: allows". A metric absent from this dict is unlimited on every plan, which is
#: a legitimate state and was `IMPORTS`' state for five phases - counted by
#: `consume()`, never once checked against anything.
_LIMIT_FIELDS = {
    UsageRecord.Metric.RUNS_STARTED: "max_runs_per_month",
    DECKS_OWNED: "max_decks",
    UsageRecord.Metric.IMPORTS: "max_imports_per_month",
}


def check(user, metric: str, amount: int = 1, *, raise_on_fail: bool = True) -> QuotaDecision:
    """Would consuming `amount` of `metric` stay within the user's plan?

    Args:
        user: The acting user.
        metric: A `UsageRecord.Metric` value.
        amount: How much would be consumed.
        raise_on_fail: Raise `QuotaExceeded` rather than returning a
            disallowed decision. Callers that want to render a friendly page
            can pass False.
    """
    plan = plan_for(user)
    limit_field = _LIMIT_FIELDS.get(metric)
    limit = getattr(plan, limit_field, None) if limit_field else None

    consumed = used(user, metric)
    if limit is None:
        return QuotaDecision(True, metric, None, consumed, None)

    allowed = consumed + amount <= limit
    decision = QuotaDecision(allowed, metric, limit, consumed, max(0, limit - consumed))
    if not allowed and raise_on_fail:
        raise QuotaExceeded(
            f"{metric}: {consumed}/{limit} used on the {plan.name} plan",
            metric=metric,
            limit=limit,
            used=consumed,
        )
    return decision


def _recorded(metric: str) -> None:
    if metric in _LIVE_COUNTS:
        raise ValueError(f"{metric} is counted from the live tables, never recorded")


@transaction.atomic
def consume(user, metric: str, amount: int = 1) -> None:
    """Record usage. Call only after `check()` has passed."""
    _recorded(metric)
    record, created = UsageRecord.objects.get_or_create(
        user=user, metric=metric, period_start=period_start(), defaults={"amount": amount}
    )
    if not created:
        UsageRecord.objects.filter(pk=record.pk).update(amount=F("amount") + amount)


@transaction.atomic
def refund(user, metric: str, amount: int = 1) -> None:
    """Give usage back after a cancelled or failed action."""
    _recorded(metric)
    UsageRecord.objects.filter(user=user, metric=metric, period_start=period_start()).update(
        amount=F("amount") - amount
    )
