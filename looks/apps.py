from django.apps import AppConfig


class LooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "looks"
    verbose_name = "Looks"

    def ready(self):
        from looks import signals  # noqa: F401 - connects the receivers
