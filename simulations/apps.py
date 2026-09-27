from django.apps import AppConfig


class SimulationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "simulations"
    verbose_name = "Simulations"

    def ready(self):
        # Registers the pre_delete receiver that gives an unfinished run's
        # concurrency slot back. Imported for the side effect, like core.checks.
        from simulations import signals  # noqa: F401
