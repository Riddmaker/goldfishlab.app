"""Admin for annotations and simulation runs.

The useful screen here is `SimulationRun`. Because progress is kept in the
database rather than in the Celery result backend, this list is a real audit
trail: which runs are in flight, how far each one got, and what a failed one
said - without opening a worker log or asking the broker anything.
"""

from django.contrib import admin
from django.utils.html import format_html, format_html_join

from simulations import unread
from simulations.models import CardAnnotation, SimulationRun, UnreadCard


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
        "copies_total",
        "copies_unreadable",
        "engine_version",
        "task_id",
        "created_at",
        "started_at",
        "finished_at",
    )


@admin.register(UnreadCard)
class UnreadCardAdmin(admin.ModelAdmin):
    """P19a: what to teach the engine next, most often seen first.

    Filled by uploads (`simulations.unread`), never by hand; the one thing
    the operator sets is a card the engine will not read (it needs an
    opponent), or one to look at again.
    """

    list_display = ("card", "seen", "status", "short_reasons", "players", "last_seen",
                    "engine_version", "read_since")
    list_filter = ("status", "engine_version")
    search_fields = ("oracle_card__name", "oracle_card__front_name")
    ordering = ("-seen",)
    actions = ("mark_wont_fix", "reopen")
    fields = ("card", "status", "reasons_list", "card_text", "scryfall", "answers_list",
              "seen", "first_seen", "last_seen", "engine_version", "read_since", "mailed_at")
    readonly_fields = tuple(field for field in fields if field != "status")

    def has_add_permission(self, request):
        return False

    @admin.display(description="card", ordering="oracle_card__name")
    def card(self, obj):
        return obj.oracle_card.name

    @admin.display(description="reasons")
    def short_reasons(self, obj):
        return "; ".join(obj.reasons)[:120]

    @admin.display(description="reasons")
    def reasons_list(self, obj):
        return format_html("<ul>{}</ul>", format_html_join("", "<li>{}</li>",
                                                          ((reason,) for reason in obj.reasons)))

    @admin.display(description="card text")
    def card_text(self, obj):
        card = obj.oracle_card
        return format_html("<strong>{}</strong> {}<br>{}<br><pre>{}</pre>",
                           card.name, card.mana_cost, card.type_line, card.oracle_text)

    @admin.display(description="Scryfall")
    def scryfall(self, obj):
        uri = obj.oracle_card.scryfall_uri
        return format_html('<a href="{}" rel="noopener">{}</a>', uri, uri) if uri else "-"

    @admin.display(description="players who answered it")
    def players(self, obj):
        return unread.answers(obj.oracle_card)[0]

    @admin.display(description="what players answered (commonest first)")
    def answers_list(self, obj):
        players, common = unread.answers(obj.oracle_card)
        if not players:
            return "nobody yet"
        return format_html("{} player(s)<ul>{}</ul>", players, format_html_join(
            "", "<li>{}x <code>{}</code></li>", ((count, value) for value, count in common)))

    @admin.action(description="Won't fix (needs an opponent): keep counting, stop mailing")
    def mark_wont_fix(self, request, queryset):
        queryset.update(status=UnreadCard.Status.WONT_FIX)

    @admin.action(description="Open again")
    def reopen(self, request, queryset):
        queryset.update(status=UnreadCard.Status.OPEN, read_since=None)
