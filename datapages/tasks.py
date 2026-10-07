"""The weekly precon refresh (P11): new precons and changed lists, without
anybody running a command. Set up like the card refresh (`cards/tasks.py`):
the long queue, acknowledged on receipt, and its own time limits - reading
thirty-odd deck files takes longer than a simulation chunk may."""

import logging

from celery import shared_task

from datapages import mtgjson, services, sweep

logger = logging.getLogger(__name__)

SOFT_TIME_LIMIT = 15 * 60
TIME_LIMIT = 20 * 60


@shared_task(name="datapages.refresh", queue="sim_long", acks_late=False,
             soft_time_limit=SOFT_TIME_LIMIT, time_limit=TIME_LIMIT)
def refresh() -> str:
    try:
        outcome = services.refresh()
    except mtgjson.MTGJSONError as exc:
        # Nothing was changed; next week tries again, or the command by hand.
        logger.warning("precon refresh: %s", exc)
        return "mtgjson unreachable"
    # The article's sweep follows the precons: a new best-read precon, or a
    # changed list, gets its variants; the others are left as they are.
    built = [what for what, _ in sweep.run()]
    return ", ".join(f"{outcome.count(what)} {what}" for what in
                     (services.CREATED, services.CHANGED, services.HELD)) + (
        f"; sweep: {len(built) - built.count(services.UNCHANGED)} built")
