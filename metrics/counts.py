"""Adding to the day's counts (P2).

Called from the places where the counted thing happens - a guest is made,
saved, a simulation starts, a shared report is read - and from the sign-up
signal. A count is never worth a failed request: a database error while
counting is logged and the request goes on.
"""

import logging
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from metrics.models import DailyCount

logger = logging.getLogger(__name__)

Name = DailyCount.Name


def week_start(day: date) -> date:
    """The Monday of `day`'s week."""
    return day - timedelta(days=day.weekday())


def add(name: str, amount: int = 1, day: date | None = None) -> None:
    """`amount` more of `name` on `day` (today in the site's time zone)."""
    day = day or timezone.localdate()
    try:
        # A savepoint: inside a caller's transaction, a failed count must not
        # break what the caller still has to write.
        with transaction.atomic():
            if not _increment(day, name, amount):
                try:
                    with transaction.atomic():
                        DailyCount.objects.create(day=day, name=name, value=amount)
                except IntegrityError:
                    # The day's first two at once: the other one made the row.
                    _increment(day, name, amount)
    except DatabaseError:
        logger.warning("could not count %s", name, exc_info=True)


def _increment(day: date, name: str, amount: int) -> int:
    return DailyCount.objects.filter(day=day, name=name).update(value=F("value") + amount)


def run_started(owner) -> None:
    """A simulation is about to be created for `owner`: count it, and the
    person if it is their first one this week.

    The account remembers the Monday of the last week it was counted in
    (`User.simulated_week`), and only the one request that moves it on counts:
    a guest who uploads a second deck - which deletes the first one's runs -
    is still one person, and two runs started at once count once. Saving a
    guest's deck carries the week over (`guests.services.claim`).
    """
    guest = getattr(owner, "is_guest", False)
    add(Name.RUN_GUEST if guest else Name.RUN_MEMBER)
    monday = week_start(timezone.localdate())
    # `exclude` keeps a row whose week is still empty (NULL).
    moved = get_user_model().objects.filter(pk=owner.pk).exclude(
        simulated_week=monday).update(simulated_week=monday)
    owner.simulated_week = monday
    if moved:
        add(Name.SIMULATOR_GUEST if guest else Name.SIMULATOR_MEMBER)
