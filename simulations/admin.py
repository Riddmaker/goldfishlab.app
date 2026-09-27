"""Admin for annotations and simulation runs.

The useful screen here is `SimulationRun`. Because progress is kept in the
database rather than in the Celery result backend, this list is a real audit
trail: which runs are in flight, how far each one got, and what a failed one
said - without opening a worker log or asking the broker anything.
"""

from django.contrib import admin

from simulations.models import CardAnnotation, SimulationRun


@admin.register(CardAnnotation)
class CardAnnotationAdmin(admin.ModelAdmin):
    list_display = ("oracle_card", "scope", "owner", "deck", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("oracle_card__front_name", "owner__email", "note")
    autocomplete_fields = ("oracle_card", "owner", "deck")


@admin.register(SimulationRun)
class SimulationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "deck",
        "owner",
        "status",
        "progress_pct",
        "games_done",
        "games_total",
        "created_at",
    )
    list_filter = ("status", "on_the_play")
    search_fields = ("deck__name", "owner__email", "task_id")
    autocomplete_fields = ("deck", "owner")
    # Everything here is written by the worker. A human editing a counter
    # mid-run would make the progress bar lie and the merge arithmetic wrong.
    readonly_fields = (
        "id",
        "games_done",
        "chunks_done",
        "chunks_total",
        "result",
        "gaps",
        "cards_total",
        "cards_with_gaps",
        "engine_version",
        "task_id",
        "created_at",
        "started_at",
        "finished_at",
    )
