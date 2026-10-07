"""The data pages (P11): every precon in one table, and one page each.

Public, indexable and the same for everybody, so nothing here reads who is
looking; counted as `data_page_opened`, robots left out, with no cookie.
"""

from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.utils.safestring import mark_safe
from django.views.generic import TemplateView
from django_ratelimit.decorators import ratelimit

from core import seo
from core.l10n import number, percent
from datapages import numbers, services
from datapages.models import Precon
from metrics import counts
from sharing.services import ROBOTS
from sharing.views import CACHE_SECONDS, rendered

#: The table's columns that sort, and what each sorts by. Newest first when
#: the address names none, or one that is not here.
SORTS = {
    "released": (lambda row: row.precon.released, True),
    "name": (lambda row: row.precon.name.casefold(), False),
    "lands": (lambda row: row.numbers.lands, True),
    "on_curve": (lambda row: row.numbers.on_curve, True),
    "kept_seven": (lambda row: row.numbers.kept_seven, True),
    "mana": (lambda row: row.numbers.mana_turn_4, True),
    "read": (lambda row: row.shared.run.readable_pct, True),
}
DEFAULT_SORT = "released"
ROWS_KEY = "datapages:rows"


class Row:
    """A precon in the table, with the numbers of its shown run. Kept in the
    cache for every language, so the labels are written when they are read."""

    def __init__(self, precon: Precon, shared):
        self.precon = precon
        self.shared = shared
        self.numbers = numbers.of(shared.run)

    on_curve = property(lambda self: percent(self.numbers.on_curve))
    kept_seven = property(lambda self: percent(self.numbers.kept_seven))
    mana = property(lambda self: number(self.numbers.mana_turn_4, 1))
    #: How much of the deck the engine read in full - the number every other
    #: one is only as good as.
    read = property(lambda self: percent(self.shared.run.readable_pct))


def rows() -> list[Row]:
    """Every published precon that has a finished run, newest first. Kept for
    as long as a report is (`sharing.views.CACHE_SECONDS`)."""
    found = cache.get(ROWS_KEY)
    if found is None:
        found = []
        for precon in Precon.objects.filter(published=True, deck__isnull=False):
            shared = services.report_of(precon)
            if shared is not None:
                found.append(Row(precon, shared))
        cache.set(ROWS_KEY, found, CACHE_SECONDS)
    return found


def count_open(request) -> None:
    agent = request.headers.get("User-Agent", "")
    if agent and not ROBOTS.search(agent):
        counts.add(counts.Name.DATA_PAGE_OPENED)


@method_decorator(ratelimit(key="ip", rate="60/m", method="GET", block=True), name="get")
class PreconListView(TemplateView):
    """/data/commander-precons/: every precon side by side."""

    template_name = "datapages/precons.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        count_open(self.request)
        sort = self.request.GET.get("sort", DEFAULT_SORT)
        if sort not in SORTS:
            sort = DEFAULT_SORT
        key, descending = SORTS[sort]
        table = sorted(rows(), key=key, reverse=descending)
        context.update({
            "rows": table,
            "sort": sort,
            "games": services.GAMES,
            "turns": services.TURNS,
            "turn": numbers.TURN,
        })
        return context


@method_decorator(ratelimit(key="ip", rate="60/m", method="GET", block=True), name="get")
class PreconView(TemplateView):
    """/data/commander-precons/<slug>/: one precon's report."""

    template_name = "datapages/precon.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        precon = get_object_or_404(Precon, slug=kwargs["slug"], published=True)
        shared = services.report_of(precon)
        if shared is None:
            raise Http404
        count_open(self.request)
        url = seo.absolute(self.request, precon.get_absolute_url())
        parts = rendered(self.request, shared, url)
        context.update({
            "precon": precon,
            "shared_report": shared,
            "run": shared.run,
            "sentences": numbers.sentences(numbers.of(shared.run)),
            "body": mark_safe(parts["body"]),  # noqa: S308 - our own template's output
            "share_description": parts["description"],
            "og_type": "article",
        })
        return context


@method_decorator(ratelimit(key="ip", rate="60/m", method="GET", block=True), name="get")
class LandsArticleView(TemplateView):
    """/data/how-many-lands-in-commander/: the land sweep, written up."""

    template_name = "datapages/lands.html"

    def get_context_data(self, **kwargs):
        from datapages import article, sweep

        context = super().get_context_data(**kwargs)
        count_open(self.request)
        context.update(article.context())
        context.update({"games": services.GAMES, "turns": services.TURNS,
                        "turn": numbers.TURN, "sweep_decks": sweep.PRECONS,
                        "og_type": "article"})
        return context
