"""Playtest URLs.

`act/`, `undo/`, `redo/` and `fork/` are all POSTs, because each of them
changes stored state - a GET that mutates a game would be replayed by every
crawler and every back button. They answer with the board fragment for htmx and
with a redirect for a browser that posted the form directly, which is the same
one-code-path rule the rest of the application follows.
"""

from django.urls import path

from playtest import views

app_name = "playtest"

urlpatterns = [
    path("decks/<uuid:deck_id>/playtest/", views.StartView.as_view(), name="start"),
    path("playtest/<uuid:pk>/", views.BoardView.as_view(), name="detail"),
    path("playtest/<uuid:pk>/act/", views.ActView.as_view(), name="act"),
    path("playtest/<uuid:pk>/undo/", views.UndoView.as_view(), name="undo"),
    path("playtest/<uuid:pk>/redo/", views.RedoView.as_view(), name="redo"),
    path("playtest/<uuid:pk>/fork/", views.ForkView.as_view(), name="fork"),
]
