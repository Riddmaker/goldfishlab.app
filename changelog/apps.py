"""Changelog app config: what changed, on a page, in a feed and by mail (C7)."""

from django.apps import AppConfig


class ChangelogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "changelog"

    def ready(self):
        from changelog import signals  # noqa: F401
