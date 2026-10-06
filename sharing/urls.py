"""Sharing URLs (P4).

The owner's two buttons hang off the run, under /runs/ like the rest of it.
The public page is /r/<token>/: short enough to paste, and outside /runs/,
which stays the owner's. It is mounted with the other public pages, which
carry their language in the address (P10, goldfishlab.urls), so it lives in a
namespace of its own: `shared:report`.
"""

from django.urls import path

from sharing import views

app_name = "sharing"

urlpatterns = [
    path("runs/<uuid:pk>/share/", views.ShareView.as_view(), name="share"),
    path("runs/<uuid:pk>/share/stop/", views.StopView.as_view(), name="stop"),
]

#: `include()`d by the root URLconf among the public pages (P10).
report_patterns = (
    [path("r/<str:token>/", views.SharedReportView.as_view(), name="report")],
    "shared",
)
