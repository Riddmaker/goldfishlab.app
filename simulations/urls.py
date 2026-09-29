"""Simulation URLs.

`progress/` is a fragment, not a page. It exists because htmx polls it; it is
scoped to the owner exactly like everything else here, so a guessed run id
returns a 404 rather than somebody else's numbers.

The `tune/` routes hang off a deck rather than a run, because a judgement about
a card outlives any one simulation of it - and because the whole point of the
honesty layer is to be reachable from a result *and* from the deck itself.
"""

from django.urls import path

from simulations import views

app_name = "simulations"

urlpatterns = [
    path("decks/<uuid:deck_id>/run/", views.RunCreateView.as_view(), name="create"),
    path("runs/<uuid:pk>/", views.RunDetailView.as_view(), name="detail"),
    path("runs/<uuid:pk>/progress/", views.RunProgressView.as_view(), name="progress"),
    path("runs/<uuid:pk>/cancel/", views.RunCancelView.as_view(), name="cancel"),
    path("decks/<uuid:pk>/tune/", views.DeckTuneView.as_view(), name="tune"),
    path(
        "decks/<uuid:pk>/tune/<uuid:oracle_id>/",
        views.CardAnnotateView.as_view(),
        name="annotate",
    ),
    path(
        "decks/<uuid:pk>/tune/<uuid:oracle_id>/forget/",
        views.AnnotationDeleteView.as_view(),
        name="forget",
    ),
]
