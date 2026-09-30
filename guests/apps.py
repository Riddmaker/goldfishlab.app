"""Guests app config: trying the site without an account (phase 9 G)."""

from django.apps import AppConfig


class GuestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "guests"
