"""Quota enforcement.

Billing does not ship until Phase 6, but the quota path is live from Phase 0
and everything downstream trusts it. These tests are the reason it can be
trusted.
"""

import pytest
from django.contrib.auth import get_user_model

from billing import quotas
from billing.models import Plan, UsageRecord

User = get_user_model()

pytestmark = pytest.mark.django_db

#: A monthly, recorded metric with a limit on the free plan (five imports).
#: It used to be `DECKS_CREATED`, until `max_decks` became a count of decks
#: owned on 2026-09-25 - see the tests at the bottom.
METRIC = UsageRecord.Metric.IMPORTS


@pytest.fixture
def user():
    return User.objects.create_user(email="q@example.com", password="pw-for-test-only")


def test_free_plan_limits_match_the_documented_tiers():
    """instructions.md publishes these numbers; keep them honest."""
    free = Plan.objects.get(slug="free")
    assert free.max_decks == 3
    assert free.max_games_per_run == 10_000
    assert free.max_turns == 9  # P5: a bracket check needs 9


def test_paid_tiers_increase_turns_not_just_games():
    """Turns simulated is the priced lever, per the planning decision.

    10k games already gives about +/-0.5pp at 95% confidence, so iterations
    are not what a paid tier should really be selling.
    """
    free = Plan.objects.get(slug="free")
    pw = Plan.objects.get(slug="koi")
    kraken = Plan.objects.get(slug="kraken")
    assert free.max_turns < pw.max_turns < kraken.max_turns


def test_plan_for_returns_the_users_plan(user):
    assert quotas.plan_for(user).slug == "free"


def test_check_allows_within_limit(user):
    decision = quotas.check(user, METRIC, 1)
    assert decision.allowed
    assert decision.limit == 5
    assert decision.remaining == 5


def test_consume_then_check_reflects_usage(user):
    quotas.consume(user, METRIC, 4)
    decision = quotas.check(user, METRIC, 1)
    assert decision.used == 4
    assert decision.remaining == 1
    assert decision.allowed


def test_check_raises_when_exceeded(user):
    quotas.consume(user, METRIC, 5)
    with pytest.raises(quotas.QuotaExceeded) as excinfo:
        quotas.check(user, METRIC, 1)
    assert excinfo.value.limit == 5
    assert excinfo.value.used == 5


def test_check_can_report_instead_of_raising(user):
    quotas.consume(user, METRIC, 5)
    decision = quotas.check(user, METRIC, 1, raise_on_fail=False)
    assert not decision.allowed


def test_refund_gives_usage_back(user):
    """Quota is debited at enqueue and refunded on cancel or failure."""
    quotas.consume(user, METRIC, 5)
    quotas.refund(user, METRIC, 2)
    assert quotas.used(user, METRIC) == 3
    assert quotas.check(user, METRIC, 1).allowed


def test_unlimited_plan_is_never_blocked(user):
    user.subscription.plan = Plan.objects.get(slug="koi")
    user.subscription.save()
    quotas.consume(user, METRIC, 10_000)
    decision = quotas.check(user, METRIC, 1)
    assert decision.allowed
    assert decision.unlimited


def test_usage_is_one_row_per_user_period_metric(user):
    quotas.consume(user, METRIC, 1)
    quotas.consume(user, METRIC, 1)
    assert UsageRecord.objects.filter(user=user, metric=METRIC).count() == 1
    assert quotas.used(user, METRIC) == 2


# --- decks: a count of what you have, not of what you made -------------------

def test_the_deck_limit_counts_decks_owned(user):
    from decks.models import Deck

    for index in range(3):
        Deck.objects.create(owner=user, name=f"Deck {index}")
    assert not quotas.check(user, quotas.DECKS_OWNED, 1, raise_on_fail=False).allowed

    Deck.objects.filter(owner=user).first().delete()
    assert quotas.check(user, quotas.DECKS_OWNED, 1).allowed, "deleting a deck frees a slot"


def test_a_new_month_does_not_hand_out_three_more_decks(user):
    """The monthly counter this replaced reset on the first of every month."""
    from decks.models import Deck

    for index in range(3):
        Deck.objects.create(owner=user, name=f"Deck {index}")
    UsageRecord.objects.all().delete()
    assert not quotas.check(user, quotas.DECKS_OWNED, 1, raise_on_fail=False).allowed


def test_a_live_count_is_never_recorded(user):
    with pytest.raises(ValueError):
        quotas.consume(user, quotas.DECKS_OWNED)
