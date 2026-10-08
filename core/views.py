"""Core views: landing page, styleguide and health check."""

import logging
from datetime import date

from django.conf import settings
from django.db import connection
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.views.generic import TemplateView

from core import seo, ticker
from core.tokens import color_families
from decks.analysis import KARSTEN_MAX, KARSTEN_MIN
from goldfishlab import celery_app
from simulations.provenance import SOURCES

logger = logging.getLogger(__name__)


class HomeView(TemplateView):
    """Landing page."""

    template_name = "core/home.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs),
                "ticker": ticker.lines_for(self.request.user)}


class TickerView(HomeView):
    """The ticker's lines alone, for the home page to ask for every 30 s."""

    template_name = "core/_ticker_lines.html"


class RobotsView(TemplateView):
    """/robots.txt (P3): every crawler welcome, AI crawlers included.

    Only what is never worth fetching is blocked. Private pages are left open
    on purpose: they say `noindex`, and a crawler that is blocked from a page
    never reads that, so a blocked page linked from elsewhere can still turn up
    in results as a bare address.
    """

    template_name = "robots.txt"
    content_type = "text/plain; charset=utf-8"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs),
                "sitemap_url": seo.absolute(self.request, reverse("sitemap"))}


class StyleguideView(TemplateView):
    """Live rendering of every design token and component.

    Kept as a real page rather than a static document so a token change is
    visible immediately and so tests/test_design_tokens.py has something to
    check against. A development tool: production answers 404.
    """

    template_name = "core/styleguide.html"

    def dispatch(self, request, *args, **kwargs):
        if not settings.DEBUG:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Read the tokens fresh in DEBUG so editing input.css shows up on
        # reload without restarting the server.
        context["families"] = color_families(use_cache=not settings.DEBUG)
        return context


class MethodologyView(TemplateView):
    """What is simulated, what is not, and how the claims were checked.

    Kept as a first-class page rather than a paragraph in the footer because it
    is the one thing a competitor selling a single "power level" number cannot
    write: every honest sentence on it costs them their product. It is also the
    page that makes the coverage score comprehensible to somebody who has not
    read six phase documents.
    """

    template_name = "core/methodology.html"

    def get_context_data(self, **kwargs):
        # Where a card's value can come from - the old card list's preamble,
        # read off the same table the card pages label their rows with.
        # The land band the deck page draws, from the constant it judges by.
        return {**super().get_context_data(**kwargs), "sources": SOURCES,
                "karsten_min": KARSTEN_MIN, "karsten_max": KARSTEN_MAX}


class TermsView(TemplateView):
    """Terms of service."""

    template_name = "core/terms.html"
    # A date, not text: each language writes it its own way (phase 12 I).
    # 2026-10-06: shared reports (P4) are published at their owner's choice.
    # 2026-10-08: plans paid monthly or yearly (P5).
    extra_context = {"updated": date(2026, 10, 8)}


class PrivacyView(TemplateView):
    """Privacy policy: what is stored, why, for how long, and how to end it."""

    template_name = "core/privacy.html"
    # 2026-10-06: shared reports (P4); usage counts (P2).
    # 2026-10-07: data pages opened are counted too (P11).
    # 2026-10-08: sponsors and shop links, Discord, Ko-fi, the EU
    # representative (D2, C1, M3, M4); accounts.consent.CONSENT_VERSION.
    # Also 2026-10-08: the changelog mail, opt-in (C7) - not a change anybody
    # must agree to again: nothing changes for whoever does not switch it on.
    extra_context = {"updated": date(2026, 10, 8)}


class ImprintView(TemplateView):
    """Legal notice: who runs this, as the Swiss UWG Art. 3 para. 1 lit. s asks."""

    template_name = "core/imprint.html"


def healthz(request):
    """Liveness and readiness probe.

    Checks the two dependencies that actually fail in production: the
    database (its node restarts on every redeploy) and Redis. Unauthenticated
    by design so Jelastic and uptime monitors can reach it.
    """
    checks = {}
    healthy = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        logger.warning("healthz: database check failed: %s", exc)
        checks["database"] = "error"
        healthy = False

    try:
        celery_app.broker_connection().ensure_connection(max_retries=0, timeout=2)
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("healthz: redis check failed: %s", exc)
        checks["redis"] = "error"
        healthy = False

    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )
