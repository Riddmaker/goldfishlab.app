"""Accounts app config."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from accounts.language import remember_on_login

        user_logged_in.connect(remember_on_login, dispatch_uid="accounts.remember_language")
