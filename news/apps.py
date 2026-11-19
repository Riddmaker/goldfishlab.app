"""The weekly news (PR 22): a page, a feed and a mail, written by a routine
and published only through a reviewed pull request."""

from django.apps import AppConfig


class NewsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "news"

    def ready(self):
        from news import signals  # noqa: F401
