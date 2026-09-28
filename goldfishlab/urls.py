"""Root URL configuration."""

from django.contrib import admin
from django.templatetags.static import static
from django.urls import include, path
from django.utils.functional import lazy
from django.views.generic import RedirectView
from django_ratelimit.decorators import ratelimit

from core.views import (
    HomeView,
    ImprintView,
    MethodologyView,
    PrivacyView,
    StyleguideView,
    TermsView,
    healthz,
)

# The admin's login is Django's own view, so neither allauth's limits nor ours
# ever saw it: until the 2026-09-25 review /admin/login/ took password guesses
# for the one account that can do everything, as fast as anybody could send
# them. Keyed on the address (through `core.ratelimit.client_ip`), because the
# person guessing is by definition not signed in.
admin.site.login = ratelimit(key="ip", rate="10/m", method="POST", block=True)(
    admin.site.login
)

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("decks/", include("decks.urls")),
    path("", include("simulations.urls")),
    path("", include("playtest.urls")),
    path("collection/", include("collection.urls")),
    path("combos/", include("combos.urls")),
    path("billing/", include("billing.urls")),
    path("styleguide/", StyleguideView.as_view(), name="styleguide"),
    path("healthz/", healthz, name="healthz"),
    # Pages carry `<link rel="icon">`; this is for whatever asks the old way
    # (the admin, a feed reader, a browser opening a JSON response). Lazy,
    # because the hashed file name is only known once the manifest is loaded,
    # and temporary, because that name changes whenever the icon does.
    path("favicon.ico", RedirectView.as_view(url=lazy(static, str)("img/favicon.svg"))),
    # The methodology page is a competitive asset rather than boilerplate:
    # saying plainly what is simulated and what is not is the thing no
    # "AI power level: 7.3" competitor can write. (Phase 8.)
    path("about/methodology/", MethodologyView.as_view(), name="methodology"),
    path("terms/", TermsView.as_view(), name="terms"),
    path("privacy/", PrivacyView.as_view(), name="privacy"),
    path("imprint/", ImprintView.as_view(), name="imprint"),
    # `/account/` is ours; `/accounts/` below is allauth's.
    path("account/", include("accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]

# Two limiters, one page. django-ratelimit raises `Ratelimited`, which is a
# `PermissionDenied` and therefore arrives as a 403; `permission_denied` sorts
# it back out into a 429. allauth looks up `handler429` in this module by name
# and calls it directly, so both paths end on templates/429.html.
handler403 = "core.ratelimit.permission_denied"
handler429 = "core.ratelimit.too_many_requests"
