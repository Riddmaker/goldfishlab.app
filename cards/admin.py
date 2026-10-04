"""Admin for the card catalogue.

Read-mostly on purpose. The catalogue is derived data: the way to change a card
is to re-run the ingestion, not to edit a row and have it silently overwritten
by the next nightly import. The admin exists to *inspect* - especially the
`needs_review` queue, which is the honest list of what the deriver could not
work out.
"""

from django.contrib import admin

from cards.models import BulkImport, DerivedProfile, OracleCard, OracleCardTag, Tag


@admin.register(BulkImport)
class BulkImportAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "scryfall_updated_at",
        "status",
        "rows_written",
        "rows_skipped",
        "peak_memory_mb",
        "sets_fingerprint",
        "started_at",
    )
    list_filter = ("kind", "status")
    readonly_fields = tuple(field.name for field in BulkImport._meta.fields)

    @admin.display(description="peak MB", ordering="peak_memory_kb")
    def peak_memory_mb(self, obj):
        return f"{obj.peak_memory_kb / 1024:.1f}" if obj.peak_memory_kb else "-"

    def has_add_permission(self, request):
        return False


class OracleCardTagInline(admin.TabularInline):
    model = OracleCardTag
    extra = 0
    fields = ("tag", "is_direct", "weight")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(OracleCard)
class OracleCardAdmin(admin.ModelAdmin):
    list_display = ("name", "mana_cost", "type_line", "game_changer", "cmc")
    list_filter = ("game_changer", "reserved", "layout")
    search_fields = ("name", "front_name", "search_name")
    inlines = [OracleCardTagInline]
    readonly_fields = ("oracle_id", "imported_at")


@admin.register(DerivedProfile)
class DerivedProfileAdmin(admin.ModelAdmin):
    list_display = ("oracle_card", "kind", "mv", "enters_tapped", "mana_amount", "needs_review")
    list_filter = ("kind", "needs_review", "enters_tapped", "produces_mana")
    search_fields = ("oracle_card__name",)
    readonly_fields = ("oracle_card", "derived_at", "source_map")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("slug", "label")
    search_fields = ("slug", "label")
