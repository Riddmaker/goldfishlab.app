"""Billing models.

These exist from Phase 0 even though Stripe does not ship until Phase 6.
That is deliberate: retrofitting a Subscription onto an existing user base
means every quota check owns a nullable branch forever.

Design rules:
  * Limits live in the DATABASE, not in settings.py, so changing a limit is
    not a deploy.
  * Every user gets a Subscription at signup, pointing at the free plan.
  * UsageRecord is append-only.
  * The Stripe webhook is the ONLY writer of Subscription.status (Phase 6).
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext, gettext_noop

#: The names the plans are seeded with (migrations 0002, 0005), marked so the
#: catalogues carry them (phase 12). `Plan.display_name` translates a plan's
#: name when it is one of these; a name the operator changed shows as typed.
SEEDED_NAMES = (gettext_noop("Free"), gettext_noop("Guest"), gettext_noop("Planeswalker"),
                gettext_noop("Archmage"))

#: What a page calls each billing interval.
INTERVALS = {"month": gettext_noop("month"), "year": gettext_noop("year")}


class Plan(models.Model):
    """A pricing tier. Seeded by data migration; `free` always exists."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=64)
    price_chf_cents = models.PositiveIntegerField(default=0)
    #: Phase 11 G (K13): the price in each currency, in cents, the same round
    #: number in each ({"chf": 400, "eur": 400, "usd": 400}). Read only while
    #: `LOCAL_PRICES` is on (`billing.currency`); `price_chf_cents` stays the
    #: price everything else reads. The Stripe price carries the same amounts
    #: as its `currency_options`, so Checkout charges what this page showed.
    prices = models.JSONField(default=dict, blank=True)
    interval = models.CharField(max_length=16, default="month")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    # Quota limits. Null means unlimited.
    max_decks = models.PositiveIntegerField(null=True, blank=True)
    max_games_per_run = models.PositiveIntegerField(default=10_000)
    max_turns = models.PositiveIntegerField(default=6)
    max_runs_per_month = models.PositiveIntegerField(null=True, blank=True)
    max_concurrent_runs = models.PositiveIntegerField(default=1)
    #: Imports have been *counted* since Phase 1 and never capped: the metric
    #: existed, `consume()` was called, and `_LIMIT_FIELDS` had no entry for it,
    #: so every check returned unlimited. This is the field that was missing.
    #: An import is a few hundred rows resolved against a 35,000-card
    #: catalogue, so it is real work, and it is also the first thing a person
    #: does - which is why the free allowance is several and not one.
    max_imports_per_month = models.PositiveIntegerField(null=True, blank=True)

    # Boolean feature gates, so a new paid feature needs no migration.
    features = models.JSONField(default=dict, blank=True)

    # Phase 6. Empty until Stripe ships.
    stripe_price_id = models.CharField(max_length=64, blank=True)

    def __str__(self) -> str:
        return self.name

    @property
    def display_name(self) -> str:
        """The name in the page's language; `name` stays what the record says."""
        return gettext(self.name) if self.name in SEEDED_NAMES else self.name

    @property
    def interval_label(self) -> str:
        """"month", in the page's language."""
        return gettext(INTERVALS[self.interval]) if self.interval in INTERVALS else self.interval

    @property
    def price_chf(self):
        """The price as francs, for display.

        Stored in cents because money in a float is how a rounding error
        becomes an invoice, and shown in francs because "CHF 400 cents" is what
        a template that forgot the difference renders - which is exactly what
        the Phase 6 screenshot pass caught.
        """
        from decimal import Decimal

        return Decimal(self.price_chf_cents) / 100

    def price_cents(self, currency: str) -> int:
        """The price in cents in this currency; francs fall back to `price_chf_cents`."""
        cents = (self.prices or {}).get(currency)
        if cents is None and currency == "chf":
            return self.price_chf_cents
        return int(cents or 0)


class Subscription(models.Model):
    """One per user, created at signup. Never null."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)

    # Phase 6.
    stripe_customer_id = models.CharField(max_length=64, blank=True)
    stripe_subscription_id = models.CharField(max_length=64, blank=True)
    #: When Stripe created the newest event this row has been changed by.
    #: Stripe neither orders deliveries nor stops retrying a failed one for
    #: days, so an `updated` event can arrive after the `deleted` that
    #: superseded it - and applying it would hand a paid plan back for good,
    #: because no later event is coming. Events older than this are ignored.
    last_event_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.user} - {self.plan}"

    @property
    def is_paid(self) -> bool:
        return not self.plan.is_default

    @property
    def is_live_paid(self) -> bool:
        """A paid subscription Stripe is still billing.

        What stops a second checkout (Stripe's Checkout always starts a new
        subscription, so a second purchase is a second monthly charge) and what
        blocks deleting the account.
        """
        return (
            self.is_paid
            and self.status in {self.Status.ACTIVE, self.Status.PAST_DUE}
            and bool(self.stripe_subscription_id)
        )

    @property
    def needs_attention(self) -> bool:
        """Show the person a banner, because something is wrong with money.

        `past_due` keeps the plan - Stripe retries a failed card for about two
        weeks, and cutting somebody off on the first failure punishes an
        expired card more harshly than a cancellation does. What must not
        happen is for that to be **silent**, which is what this property is
        for: the entitlement survives and the page says why.
        """
        return self.status == self.Status.PAST_DUE

    @property
    def ends_soon(self) -> bool:
        """Cancelled in the portal, still inside the period already paid for."""
        return self.cancel_at_period_end and self.status == self.Status.ACTIVE


class UsageRecord(models.Model):
    """Append-only monthly counters, one row per (user, period, metric)."""

    class Metric(models.TextChoices):
        RUNS_STARTED = "runs_started", "Runs started"
        GAMES_SIMULATED = "games_simulated", "Games simulated"
        IMPORTS = "imports", "Imports"
        DECKS_CREATED = "decks_created", "Decks created"
        #: Recorded for guests only (P1): their one free summary, counted
        #: where a new upload cannot delete it with the deck.
        SUMMARIES_WRITTEN = "summaries_written", "Summaries written"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage"
    )
    period_start = models.DateField()
    metric = models.CharField(max_length=32, choices=Metric.choices)
    amount = models.BigIntegerField(default=0)

    class Meta:
        unique_together = ("user", "period_start", "metric")
        indexes = [models.Index(fields=["user", "period_start"])]

    def __str__(self) -> str:
        return f"{self.user} {self.period_start} {self.metric}={self.amount}"


class StripeEvent(models.Model):
    """Idempotency guard for the Phase 6 webhook.

    Stripe retries deliveries. Recording the event id first and bailing if it
    already existed is the single most common Stripe integration bug avoided.
    """

    stripe_event_id = models.CharField(max_length=64, unique=True)
    type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.type} {self.stripe_event_id}"
