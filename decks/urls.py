"""Deck URLs."""

from django.urls import path

from decks import views

app_name = "decks"

urlpatterns = [
    path("", views.DeckListView.as_view(), name="list"),
    path("import/", views.DeckImportView.as_view(), name="import"),
    path("imports/<uuid:pk>/", views.ImportReviewView.as_view(), name="review"),
    # "pending" cannot collide with the uuid route above, and keeping the two
    # under one prefix says what they are: two halves of the same upload.
    path("imports/pending/<uuid:pk>/", views.ImportMappingView.as_view(), name="map"),
    path(
        "imports/pending/<uuid:pk>/preview/",
        views.ImportPreviewView.as_view(),
        name="map-preview",
    ),
    path("<uuid:pk>/", views.DeckDetailView.as_view(), name="detail"),
    path("<uuid:pk>/commander/", views.SetCommanderView.as_view(), name="set-commander"),
    path("<uuid:pk>/delete/", views.DeckDeleteView.as_view(), name="delete"),
]
