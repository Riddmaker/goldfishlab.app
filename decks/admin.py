"""Admin for decks and imports.

The useful screen here is `DeckImport`: its rung counts and its unresolved rows
are the record of how exact a given import actually was.
"""

from django.contrib import admin

from decks.models import Deck, DeckCard, DeckImport, MulliganDecision, UnresolvedRow


class DeckCardInline(admin.TabularInline):
    model = DeckCard
    extra = 0
    autocomplete_fields = ("oracle_card",)


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "commander", "card_count", "updated_at")
    list_filter = ("format",)
    search_fields = ("name", "owner__email")
    autocomplete_fields = ("commander", "partner")
    inlines = [DeckCardInline]


class UnresolvedRowInline(admin.TabularInline):
    model = UnresolvedRow
    extra = 0
    readonly_fields = ("line_number", "raw_name", "quantity", "reason", "suggestions")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DeckImport)
class DeckImportAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "owner",
        "parser",
        "rows_total",
        "rows_resolved",
        "rows_unresolved",
        "created_at",
    )
    list_filter = ("parser", "status")
    readonly_fields = ("rung_counts", "created_at")
    inlines = [UnresolvedRowInline]


@admin.register(MulliganDecision)
class MulliganDecisionAdmin(admin.ModelAdmin):
    """The answers of "Keep or mulligan?" (PR 25), to read: where players and
    the simulation disagree, and the engine's own rule beside them. Nobody
    edits an answer."""

    list_display = ("created_at", "choice", "sure", "verdict", "right", "rule_keeps",
                    "decision_ms", "engine_version")
    list_filter = ("verdict", "choice", "right", "rule_keeps", "sure", "engine_version")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
