"""Core app config."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Registers the deployment checks. Importing for the side effect is
        # how Django's own apps do this; `ready()` is the only place it can
        # happen without importing settings at module import time.
        from core import checks  # noqa: F401
