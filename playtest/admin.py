"""Admin for playtest sessions.

The useful screen is the action list of one session. Because a game is stored
as its actions rather than as a state, reading that list *is* reading the game:
what was played, in what order, and what was taken back - which is the only way
to answer "it forgot my turn" without a debugger.
"""

from django.contrib import admin

from playtest.models import PlaytestAction, PlaytestSession


class PlaytestActionInline(admin.TabularInline):
    model = PlaytestAction
    extra = 0
    fields = ("seq", "kind", "payload", "undone", "created_at")
    readonly_fields = ("created_at",)
    ordering = ("seq",)


@admin.register(PlaytestSession)
class PlaytestSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "deck", "owner", "seed", "coverage_pct",
                    "forked_from", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("deck__name", "owner__email")
    autocomplete_fields = ("deck", "owner")
    readonly_fields = ("created_at", "updated_at", "coverage_pct")
    inlines = (PlaytestActionInline,)


@admin.register(PlaytestAction)
class PlaytestActionAdmin(admin.ModelAdmin):
    list_display = ("session", "seq", "kind", "undone", "created_at")
    list_filter = ("kind", "undone")
