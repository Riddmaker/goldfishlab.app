"""Root URL configuration."""

from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.templatetags.static import static
from django.urls import include, path
from django.utils.functional import lazy
from django.views.generic import RedirectView
from django_ratelimit.decorators import ratelimit

from accounts.language import set_language
from billing.views import PricingView
from core.seo import PublicPagesSitemap
from core.views import (
    HomeView,
    ImprintView,
    MethodologyView,
    PrivacyView,
    RobotsView,
    StyleguideView,
    TermsView,
    TickerView,
    healthz,
)
from metrics.views import stats
from sharing.urls import report_patterns

# The admin's login is Django's own view, so neither allauth's limits nor ours
# ever saw it: until the 2026-09-25 review /admin/login/ took password guesses
# for the one account that can do everything, as fast as anybody could send
# them. Keyed on the address (through `core.ratelimit.client_ip`), because the
# person guessing is by definition not signed in.
admin.site.login = ratelimit(key="ip", rate="10/m", method="POST", block=True)(
    admin.site.login
)

urlpatterns = [
    path("ticker/", TickerView.as_view(), name="ticker"),
    path("decks/", include("decks.urls")),
    path("", include("simulations.urls")),
    path("", include("playtest.urls")),
    # P4: /runs/<id>/share/ for the owner; /r/<token>/ is below.
    path("", include("sharing.urls")),
    path("combos/", include("combos.urls")),
    path("billing/", include("billing.urls")),
    path("styleguide/", StyleguideView.as_view(), name="styleguide"),
    path("healthz/", healthz, name="healthz"),
    # P3, search basics: core.seo has the list of public pages behind both.
    path("robots.txt", RobotsView.as_view(), name="robots"),
    path("sitemap.xml", sitemap, {"sitemaps": {"pages": PublicPagesSitemap}}, name="sitemap"),
    # Pages carry `<link rel="icon">`; this is for whatever asks the old way
    # (the admin, a feed reader, a browser opening a JSON response). Lazy,
    # because the hashed file name is only known once the manifest is loaded,
    # and temporary, because that name changes whenever the icon does.
    path("favicon.ico", RedirectView.as_view(url=lazy(static, str)("img/favicon.svg"))),
    # The same for iOS, which asks for both names at the root whether a page
    # links the icon or not (phase 12 J30; they were 404s).
    *[path(name, RedirectView.as_view(url=lazy(static, str)("img/apple-touch-icon.png")))
      for name in ("apple-touch-icon.png", "apple-touch-icon-precomposed.png")],
    # `/account/` is ours; `/accounts/` below is allauth's.
    path("account/", include("accounts.urls")),
    path("accounts/", include("allauth.urls")),
    # P2: how the site is used, staff only. Before admin/, which would
    # otherwise answer it with its own 404.
    path("admin/stats/", admin.site.admin_view(stats), name="stats"),
    path("admin/", admin.site.urls),
    # The footer's language switcher (phase 12). Outside `/account/`, which the
    # guest fence closes, so a guest can switch as well.
    path("i18n/", set_language, name="set_language"),
]

# P10: the pages a search engine should find carry their language in the
# address - /pricing/ in English, /de/pricing/ in German - so each language
# version is a page of its own that Google can index, with hreflang naming the
# others (core.seo). Only these: the app, the account screens, allauth, the
# webhooks and the admin keep one address, so no link in a mail, at Stripe or
# in a bookmark moves. Which language a page is in, and who is sent from the
# English address to their own: accounts.middleware.
urlpatterns += i18n_patterns(
    path("", HomeView.as_view(), name="home"),
    path("try/", include("guests.urls")),
    # P4: a shared report, for everybody. Its link is the English address and
    # names no language (sharing.models); a reader is sent to theirs.
    path("", include(report_patterns)),
    # The methodology page is a competitive asset rather than boilerplate:
    # saying plainly what is simulated and what is not is the thing no
    # "AI power level: 7.3" competitor can write. (Phase 8.)
    path("about/methodology/", MethodologyView.as_view(), name="methodology"),
    # Outside /billing/, which is the account's own page and closed to guests.
    path("pricing/", PricingView.as_view(), name="pricing"),
    path("terms/", TermsView.as_view(), name="terms"),
    path("privacy/", PrivacyView.as_view(), name="privacy"),
    path("imprint/", ImprintView.as_view(), name="imprint"),
    prefix_default_language=False,
)

# Two limiters, one page. django-ratelimit raises `Ratelimited`, which is a
# `PermissionDenied` and therefore arrives as a 403; `permission_denied` sorts
# it back out into a 429. allauth looks up `handler429` in this module by name
# and calls it directly, so both paths end on templates/429.html.
handler403 = "core.ratelimit.permission_denied"
handler429 = "core.ratelimit.too_many_requests"
