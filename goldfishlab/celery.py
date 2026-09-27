"""Celery application.

Long-lived worker processes are the classic source of "server closed the
connection unexpectedly" against a database node that restarts on redeploy,
hence the close_old_connections signal handlers.
"""

import os

from celery import Celery
from celery.signals import task_postrun, task_prerun
from django.db import close_old_connections

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "goldfishlab.settings.dev")

app = Celery("goldfishlab")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@task_prerun.connect
def _close_before(**_kwargs):
    close_old_connections()


@task_postrun.connect
def _close_after(**_kwargs):
    close_old_connections()


@app.task(name="goldfishlab.ping")
def ping() -> str:
    """Prove the broker round-trip end to end. Used by /healthz/ and by CI."""
    return "pong"
