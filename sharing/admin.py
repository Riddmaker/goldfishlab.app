"""Shared reports in the admin: to see what is public, and to take a link down."""

from django.contrib import admin

from sharing.models import SharedReport


@admin.register(SharedReport)
class SharedReportAdmin(admin.ModelAdmin):
    list_display = ("deck_name", "commander", "views", "created_at")
    search_fields = ("deck_name", "commander", "token")
    readonly_fields = ("run", "token", "deck_name", "commander", "cards", "summary",
                       "summary_language", "views", "created_at")

    def has_add_permission(self, request):
        return False
