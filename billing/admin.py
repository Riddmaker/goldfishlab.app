"""Billing admin."""

from django.contrib import admin

from billing.models import Plan, StripeEvent, Subscription, UsageRecord


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "price_chf_cents", "prices", "max_turns",
                    "max_games_per_run", "is_default", "is_active")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "current_period_end")
    list_filter = ("status", "plan")


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = ("user", "period_start", "metric", "amount")
    list_filter = ("metric", "period_start")


@admin.register(StripeEvent)
class StripeEventAdmin(admin.ModelAdmin):
    list_display = ("stripe_event_id", "type", "processed_at")
