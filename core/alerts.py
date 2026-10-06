"""Mail the operator when something is broken (phase 12 J12).

Without this a failed run, a queue that stopped moving or a nightly catalogue
job that failed was found only by looking. No new service: the beat checks
once an hour and mails `ALERT_EMAIL` through the SMTP the site already uses.
Sentry stays off - the privacy policy names no error reporter.

**At most one mail per kind of problem per day.** A broken worker fails every
run; one mail says so, the next day's says what happened since. The time of
the last mail per kind is in the cache; when the cache lost it, the window is
the last day.

The mails carry ids, counts and the first line of an error - never an email
address, a deck name or anything else about a person.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

#: How often one kind of problem may be mailed.
QUIET = timedelta(days=1)

#: A run nobody has started after this long: the queue is not moving.
PENDING_TOO_LONG = timedelta(minutes=10)
#: A run still running after this long: a 50,000-game run takes minutes.
RUNNING_TOO_LONG = timedelta(minutes=30)
#: A catalogue load still marked running after this long was killed.
IMPORT_TOO_LONG = timedelta(hours=1)

#: How many cases one mail lists; the count is always complete.
LISTED = 20


@dataclass
class Problem:
    kind: str
    subject: str
    lines: list[str]


def _last_mailed_key(kind: str) -> str:
    return f"alerts:last:{kind}"


def _since(kind: str, now):
    return cache.get(_last_mailed_key(kind)) or now - QUIET


def _first_line(text: str) -> str:
    return (text.strip().splitlines() or [""])[0][:200]


def _run_line(run) -> str:
    path = reverse("admin:simulations_simulationrun_change", args=[run.pk])
    return f"- {run.pk} ({run.status}, {run.games_total} games) {path}"


def failed_runs(now) -> Problem | None:
    from simulations.models import SimulationRun

    runs = list(
        SimulationRun.objects.filter(
            status=SimulationRun.Status.FAILED, finished_at__gt=_since("failed_runs", now)
        ).order_by("-finished_at")
    )
    if not runs:
        return None
    lines = [f"{_run_line(run)}\n  {_first_line(run.error)}" for run in runs[:LISTED]]
    return Problem("failed_runs", f"{len(runs)} simulation run(s) failed", lines)


def stuck_runs(now) -> Problem | None:
    from django.db.models import Q

    from simulations.models import SimulationRun

    runs = list(
        SimulationRun.objects.filter(
            Q(status=SimulationRun.Status.PENDING, created_at__lt=now - PENDING_TOO_LONG)
            | Q(status=SimulationRun.Status.RUNNING, started_at__lt=now - RUNNING_TOO_LONG)
        ).order_by("created_at")
    )
    if not runs:
        return None
    lines = [_run_line(run) for run in runs[:LISTED]]
    lines.append(
        f"Pending for more than {PENDING_TOO_LONG}, or running for more than "
        f"{RUNNING_TOO_LONG}: is a worker down?"
    )
    return Problem("stuck_runs", f"{len(runs)} simulation run(s) not moving", lines)


def failed_catalogue(now) -> Problem | None:
    from django.db.models import Q

    from cards.models import BulkImport

    imports = list(
        BulkImport.objects.filter(
            Q(status=BulkImport.Status.FAILED, started_at__gt=_since("catalogue", now))
            | Q(status=BulkImport.Status.RUNNING, started_at__lt=now - IMPORT_TOO_LONG)
        ).order_by("-started_at")
    )
    if not imports:
        return None
    lines = [
        f"- {row.kind} {row.status} at {timezone.localtime(row.started_at):%Y-%m-%d %H:%M}\n"
        f"  {_first_line(row.message)}"
        for row in imports[:LISTED]
    ]
    lines.append("The next night tries again; `ingest_scryfall` loads by hand.")
    return Problem("catalogue", "the card catalogue job failed", lines)


#: Mail when this share of the day's summary budget is used (P1).
BUDGET_WARNING = 0.8


def summary_budget(now) -> Problem | None:
    """Today's deck summaries near or at their ceiling (`simulations.budget`)."""
    from simulations import budget

    total, guest_share = budget.limits()
    guests, members = budget.counts(timezone.localdate(now))
    if total <= 0 or guests + members < BUDGET_WARNING * total:
        return None
    lines = [
        f"- {guests + members} of {total} summaries started today "
        f"({guests} by guests, whose share is {guest_share}).",
        "At the ceiling, summaries pause until midnight and nobody is charged.",
        "Raise SUMMARIES_PER_DAY / GUEST_SUMMARIES_PER_DAY if the bill allows it.",
    ]
    return Problem("summary_budget", "the daily summary budget is nearly spent", lines)


CHECKS = (failed_runs, stuck_runs, failed_catalogue, summary_budget)


def check(now=None) -> list[str]:
    """Run every check and mail what is due. Returns the kinds mailed."""
    now = now or timezone.now()
    recipient = settings.ALERT_EMAIL
    mailed = []
    for found in (check_one(now) for check_one in CHECKS):
        if found is None:
            continue
        last = cache.get(_last_mailed_key(found.kind))
        if last is not None and now - last < QUIET:
            continue
        if recipient:
            body = "\n".join(
                [f"Goldfish Lab: {found.subject}.", "", *found.lines, "",
                 "At most one mail like this a day (core/alerts.py)."]
            )
            send_mail(f"[Goldfish Lab] {found.subject}", body, None, [recipient])
            mailed.append(found.kind)
        else:
            # The same once a day in the log, rather than every hour.
            logger.warning("alert (ALERT_EMAIL unset): %s", found.subject)
        # Kept until replaced: it is also where the next window starts. Set
        # only after the mail went out, so a failed send is tried next hour.
        cache.set(_last_mailed_key(found.kind), now, timeout=None)
    return mailed
