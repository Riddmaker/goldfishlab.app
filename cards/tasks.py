"""The nightly catalogue job (phase 12 J18). The rule itself is in `cards/refresh.py`.

Three settings differ from every other task here, each for a reason:

- **`sim_long`, not the default queue.** worker-short runs one task at a time,
  and a few minutes of ingest would hold up every guest run on /try/. The long
  worker has two slots and more room to grow.
- **Acknowledged on receipt (`acks_late=False`).** With late acks an unfinished
  task goes back to the broker after `visibility_timeout` (600 s, base.py) - a
  full load can take longer, and a second worker would then load it again,
  beside the first. Lost instead: the next night simply tries again.
- **Its own time limits**: the global 120/180 s are for simulation chunks.

Every failure leaves a FAILED `BulkImport` row, which `core.alerts` mails.
"""

import logging

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from cards import refresh
from cards.models import BulkImport

logger = logging.getLogger(__name__)

SOFT_TIME_LIMIT = 25 * 60
TIME_LIMIT = 30 * 60

#: Held while a refresh runs, so a late one never runs beside another.
LOCK = "cards:refresh:running"


@shared_task(
    name="cards.refresh",
    queue="sim_long",
    acks_late=False,
    soft_time_limit=SOFT_TIME_LIMIT,
    time_limit=TIME_LIMIT,
)
def refresh_catalogue() -> str:
    if not cache.add(LOCK, True, timeout=TIME_LIMIT):
        logger.warning("cards refresh: another one is still running")
        return "skipped: another refresh is running"

    started = timezone.now()
    try:
        line = refresh.nightly(timezone.localdate())
    except Exception as exc:
        _record_failure(started, exc)
        logger.exception("cards refresh failed")
        raise
    finally:
        cache.delete(LOCK)

    logger.info("cards refresh: %s", line)
    return line


def _record_failure(started, exc: Exception) -> None:
    """A failed load has its row already (`ingest._run`); a failed pre-check does not."""
    if BulkImport.objects.filter(
        status=BulkImport.Status.FAILED, started_at__gte=started
    ).exists():
        return
    BulkImport.objects.create(
        kind=BulkImport.Kind.ORACLE_CARDS,
        # Required, and never compared for a failed row: `_already_done` only
        # looks at OK rows.
        scryfall_updated_at=started,
        status=BulkImport.Status.FAILED,
        message=f"nightly job: {type(exc).__name__}: {exc}"[:2000],
        finished_at=timezone.now(),
    )
