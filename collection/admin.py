"""Admin for collections.

Read-mostly on purpose: a collection is replaced wholesale by an import, so
editing one item by hand produces a state the next import silently discards.
"""

from django.contrib import admin

from collection.models import Collection, CollectionItem


class CollectionItemInline(admin.TabularInline):
    model = CollectionItem
    extra = 0
    fields = ("oracle_card", "quantity", "set_code", "collector_number", "finish")
    readonly_fields = fields
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("owner", "distinct_cards", "total_cards",
                    "source_filename", "imported_at")
    search_fields = ("owner__email", "source_filename")
    readonly_fields = ("created_at", "updated_at", "imported_at")
    inlines = [CollectionItemInline]
