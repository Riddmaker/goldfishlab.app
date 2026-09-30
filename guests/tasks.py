"""The guest lifetime, enforced: deleted a day after arriving, saved or not."""

import logging

from celery import shared_task

from guests import services

logger = logging.getLogger(__name__)


@shared_task(name="guests.expire")
def expire() -> int:
    deleted = services.expire()
    logger.info("guests expired: %s", deleted)
    return deleted
