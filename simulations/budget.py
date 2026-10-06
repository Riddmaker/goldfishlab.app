"""The daily ceiling on deck summaries (P1, load protection).

Every summary is a paid call to Mistral, and nothing bounded how many a day a
crowd could start - a good Reddit post would have been a bill. Now at most
`SUMMARIES_PER_DAY` are started a day (Europe/Zurich), and guests take at most
`GUEST_SUMMARIES_PER_DAY` of them, so visitors never spend the members' share.

* **Reserved where it is charged.** `summary.claim` reserves a slot inside the
  transaction that charges the run, by a conditional update: two runs started
  together cannot both take the last slot, and a rolled-back start gives its
  slot back with everything else.
* **A failure without an answer gives the slot back** (`release`): Mistral was
  down, nothing was billed. An answer that did not check out was billed, so it
  keeps its slot.
* **At the ceiling** nobody is charged for a summary that is not written: the
  run goes ahead without one, and the report says summaries pause until
  midnight.

The counts are numbers per day and nothing about anybody.
"""

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from simulations.models import SummaryDay


def today():
    """The budget's day, in the site's time zone like the monthly quotas."""
    return timezone.localdate()


def limits() -> tuple[int, int]:
    """(all summaries, guests' share) a day, as configured."""
    return settings.SUMMARIES_PER_DAY, settings.GUEST_SUMMARIES_PER_DAY


def counts(day=None) -> tuple[int, int]:
    """(guests, members) summaries started on `day`, today by default."""
    row = SummaryDay.objects.filter(day=day or today()).first()
    return (row.guests, row.members) if row else (0, 0)


def available(is_guest: bool) -> bool:
    """Is there a slot left today for this kind of owner? Decides nothing:
    `reserve` takes the slot."""
    total, guest_share = limits()
    guests, members = counts()
    if guests + members >= total:
        return False
    return not is_guest or guests < guest_share


def reserve(is_guest: bool):
    """Take one of today's slots. Returns the day it was taken on, or None.

    Call inside the transaction that charges for the summary.
    """
    day = today()
    total, guest_share = limits()
    SummaryDay.objects.get_or_create(day=day)
    rows = SummaryDay.objects.filter(day=day).alias(
        started=F("guests") + F("members")).filter(started__lt=total)
    if is_guest:
        rows = rows.filter(guests__lt=guest_share)
        taken = rows.update(guests=F("guests") + 1)
    else:
        taken = rows.update(members=F("members") + 1)
    return day if taken else None


def release(day, is_guest: bool) -> None:
    """Give a slot back to the day it was taken on - never below zero."""
    if day is None:
        return
    field = "guests" if is_guest else "members"
    SummaryDay.objects.filter(day=day, **{f"{field}__gt": 0}).update(
        **{field: F(field) - 1})
