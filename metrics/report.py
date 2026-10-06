"""The numbers on the stats page and in `manage.py stats` (P2).

Weeks run Monday to Sunday in the site's time zone (settings.TIME_ZONE).

* **The lead metric** - people who simulated at least one deck in a week,
  guests and accounts together - against the launch plan's targets. A week's
  sum of the `simulator_*` counts; the last *complete* week is the one held
  against the next target.
* **The funnel** - guests started and saved, accounts opened, simulations,
  shared reports opened - from the same daily counts.
* **Coming back** - of the accounts whose first simulation fell in a week,
  how many simulated again on another day within 7 and within 30 days. Read
  from the simulation runs that still exist: a guest who never saved is gone
  with its runs, and an account that deleted the deck of its first run starts
  later than it did. A week shows its share only once all of its people have
  had the whole 7 or 30 days.
* **Gate G3** (P9) - clicks on "Compare two versions" against reports viewed,
  since counting began.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from metrics import doors
from metrics.counts import Name, week_start
from metrics.models import DailyCount


@dataclass(frozen=True)
class Target:
    people: int
    by: date


#: The launch plan's lead-metric targets: people simulating at least one deck
#: a week, by the end of each date.
TARGETS = (
    Target(250, date(2026, 12, 31)),
    Target(400, date(2027, 3, 31)),
    Target(800, date(2027, 9, 30)),
)

#: The columns of the weekly table, in order.
COLUMNS = (
    Name.SIMULATOR_GUEST,
    Name.SIMULATOR_MEMBER,
    Name.GUEST_STARTED,
    Name.GUEST_SAVED,
    Name.SIGNUP,
    Name.RUN_GUEST,
    Name.RUN_MEMBER,
    Name.REPORT_OPENED,
    Name.REPORT_VIEWED,
    Name.COMPARE_CLICKED,
)

#: Days after the first simulation that count as coming back.
RETURN_WINDOWS = (7, 30)


def next_target(today: date) -> Target | None:
    """The first target whose date has not passed, or None after the last."""
    return next((target for target in TARGETS if target.by >= today), None)


@dataclass
class Week:
    start: date
    complete: bool
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def end(self) -> date:
        return self.start + timedelta(days=6)

    @property
    def simulators(self) -> int:
        """The lead metric."""
        return self.counts.get(Name.SIMULATOR_GUEST, 0) + self.counts.get(Name.SIMULATOR_MEMBER, 0)

    @property
    def cells(self) -> list[int]:
        return [self.counts.get(name, 0) for name in COLUMNS]


def weeks(today: date | None = None, count: int = 12) -> list[Week]:
    """The last `count` weeks, this one (so far) first."""
    today = today or timezone.localdate()
    this = week_start(today)
    result = {this - timedelta(weeks=n): Week(this - timedelta(weeks=n), complete=n > 0)
              for n in range(count)}
    rows = DailyCount.objects.filter(day__gte=min(result), day__lte=today)
    for day, name, value in rows.values_list("day", "name", "value"):
        week = result[week_start(day)]
        week.counts[name] = week.counts.get(name, 0) + value
    return list(result.values())


@dataclass
class Lead:
    """The last complete week against the next target."""

    week: Week | None
    target: Target | None

    @property
    def percent(self) -> int | None:
        if self.week is None or self.target is None:
            return None
        return round(100 * self.week.simulators / self.target.people)


def lead(today: date | None = None, recent: list[Week] | None = None) -> Lead:
    today = today or timezone.localdate()
    recent = recent if recent is not None else weeks(today, count=2)
    last = next((week for week in recent if week.complete), None)
    return Lead(week=last, target=next_target(today))


@dataclass
class Cohort:
    start: date
    people: int = 0
    back: dict[int, int] = field(default_factory=lambda: dict.fromkeys(RETURN_WINDOWS, 0))
    #: Windows every person in the cohort has had in full.
    ripe: dict[int, bool] = field(default_factory=dict)

    def percent(self, days: int) -> int | None:
        """Share that came back within `days`, or None while it is too early
        to say or nobody started in the week."""
        if not self.people or not self.ripe.get(days):
            return None
        return round(100 * self.back[days] / self.people)

    # For the template, which cannot pass an argument.
    percent_7 = property(lambda self: self.percent(7))
    percent_30 = property(lambda self: self.percent(30))
    back_7 = property(lambda self: self.back[7])
    back_30 = property(lambda self: self.back[30])


def cohorts(today: date | None = None, count: int = 12) -> list[Cohort]:
    """Accounts by the week of their first simulation, the latest week first."""
    from simulations.models import SimulationRun

    today = today or timezone.localdate()
    this = week_start(today)
    result = {}
    for n in range(count):
        start = this - timedelta(weeks=n)
        # The cohort's last person started on its Sunday.
        last_start = start + timedelta(days=6)
        result[start] = Cohort(start, ripe={days: last_start + timedelta(days=days) < today
                                            for days in RETURN_WINDOWS})

    zone = ZoneInfo(settings.TIME_ZONE)
    days_by_owner = defaultdict(set)
    owner_days = (SimulationRun.objects.filter(owner__is_guest=False)
                  .annotate(day=TruncDate("created_at", tzinfo=zone))
                  .values_list("owner_id", "day").distinct())
    for owner, day in owner_days:
        days_by_owner[owner].add(day)

    for days in days_by_owner.values():
        first = min(days)
        cohort = result.get(week_start(first))
        if cohort is None:
            continue
        cohort.people += 1
        later = [(day - first).days for day in days if day > first]
        for window in RETURN_WINDOWS:
            if any(gap <= window for gap in later):
                cohort.back[window] += 1
    return list(result.values())


@dataclass
class Gate:
    """The fake door against its gate (launch plan G3)."""

    views: int
    clicks: int

    @property
    def percent(self) -> float | None:
        return round(100 * self.clicks / self.views, 1) if self.views else None

    @property
    def judged(self) -> bool:
        """Enough views for the share to mean something."""
        return self.views >= doors.GATE_VIEWS

    @property
    def passed(self) -> bool:
        return self.judged and self.percent >= doors.GATE_PERCENT

    views_needed = doors.GATE_VIEWS
    percent_needed = doors.GATE_PERCENT


def gate() -> Gate:
    totals = dict(DailyCount.objects.filter(name__in=[Name.REPORT_VIEWED, Name.COMPARE_CLICKED])
                  .values("name").annotate(total=Sum("value")).values_list("name", "total"))
    return Gate(views=totals.get(Name.REPORT_VIEWED, 0),
                clicks=totals.get(Name.COMPARE_CLICKED, 0))
