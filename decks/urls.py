"""Deck URLs."""

from django.urls import path

from decks import photo_views, views
from looks.views import LookView

app_name = "decks"

urlpatterns = [
    # B-10: the account's mat and sleeves, reached from (and shown on) a deck.
    path("<uuid:pk>/look/", LookView.as_view(), name="look"),
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
    # B-14: a deck from photos of the cards.
    path("photos/", photo_views.PhotoUploadView.as_view(), name="photos"),
    path("photos/<uuid:pk>/", photo_views.PhotoCheckView.as_view(), name="photos-check"),
    path("<uuid:pk>/", views.DeckDetailView.as_view(), name="detail"),
    path("<uuid:pk>/commander/", views.SetCommanderView.as_view(), name="set-commander"),
    path("<uuid:pk>/printing-art/", views.PrintingArtRetryView.as_view(),
         name="printing-art"),
    path("<uuid:pk>/marks/", views.DeckMarkView.as_view(), name="mark"),
    path("<uuid:pk>/tutor/", views.HandTutorView.as_view(), name="tutor"),
    path("<uuid:pk>/mulligan/", views.MulliganQuizView.as_view(), name="mulligan"),
    path("<uuid:pk>/hand/", views.DeckInHandView.as_view(), name="hand"),
    path("<uuid:pk>/versions/new/", views.VersionNewView.as_view(), name="version-new"),
    path("<uuid:pk>/versions/<int:number>/choose/", views.VersionChooseView.as_view(),
         name="version-choose"),
    path("<uuid:pk>/versions/<int:number>/remove/", views.VersionRemoveView.as_view(),
         name="version-remove"),
    path("<uuid:pk>/rename/", views.DeckRenameView.as_view(), name="rename"),
    path("<uuid:pk>/delete/", views.DeckDeleteView.as_view(), name="delete"),
]
