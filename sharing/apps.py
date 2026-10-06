"""Sharing app config: a report anybody can read, by its owner's choice (P4)."""

from django.apps import AppConfig


class SharingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sharing"
