"""The precons in the admin (P11): what was imported, what is held and why,
and the brake - unpublish, and the page answers 404."""

from django.contrib import admin

from datapages.models import Precon


@admin.register(Precon)
class PreconAdmin(admin.ModelAdmin):
    list_display = ("name", "set_code", "released", "published", "source", "held")
    list_filter = ("published", "source", "set_code")
    search_fields = ("name", "set_code", "set_name")
    readonly_fields = ("slug", "name", "set_code", "set_name", "released", "source",
                       "source_key", "list_print", "deck", "unmatched", "created_at",
                       "updated_at")
    fields = ("published", *readonly_fields)
    actions = ("publish", "unpublish")

    def has_add_permission(self, request):
        return False

    @admin.display(boolean=True, description="Held")
    def held(self, precon):
        return bool(precon.unmatched)

    @admin.action(description="Publish the selected precons")
    def publish(self, request, queryset):
        queryset.update(published=True)

    @admin.action(description="Unpublish the selected precons")
    def unpublish(self, request, queryset):
        queryset.update(published=False)
