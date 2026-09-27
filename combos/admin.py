"""Admin for the combo mirror.

Read-only on purpose: every row here is somebody else's data, fetched from
Commander Spellbook and refreshable in one click from a deck page. A hand-edit
would be silently overwritten by the next lookup, which is a worse outcome than
not being able to make one.
"""

from django.contrib import admin

from combos.models import (
    Combo,
    ComboCard,
    ComboLookup,
    ComboMeasurement,
    ComboTemplate,
    DeckCombo,
)


class ComboCardInline(admin.TabularInline):
    model = ComboCard
    extra = 0


class ComboTemplateInline(admin.TabularInline):
    model = ComboTemplate
    extra = 0


@admin.register(Combo)
class ComboAdmin(admin.ModelAdmin):
    list_display = ("spellbook_id", "identity", "popularity", "status", "refreshed_at")
    list_filter = ("identity", "status", "legal_commander")
    search_fields = ("spellbook_id", "cards__name")
    inlines = [ComboCardInline, ComboTemplateInline]


class DeckComboInline(admin.TabularInline):
    model = DeckCombo
    extra = 0


@admin.register(ComboLookup)
class ComboLookupAdmin(admin.ModelAdmin):
    list_display = ("deck", "status", "fetched_at", "attempted_at", "out_of_identity")
    list_filter = ("status",)
    inlines = [DeckComboInline]


@admin.register(ComboMeasurement)
class ComboMeasurementAdmin(admin.ModelAdmin):
    """Read-only, and for a different reason than the rest of this file.

    A measurement is a record of what one simulation actually observed. Editing
    one by hand would not be overwritten by anything - it would simply be a
    number on a page that no run ever produced, which is the one output this
    application exists to make impossible.
    """

    list_display = ("combo", "deck", "kind", "added_name", "share", "games", "turns")
    list_filter = ("kind", "turns")
    search_fields = ("combo__spellbook_id", "deck__name", "added_name")
    readonly_fields = [field.name for field in ComboMeasurement._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
