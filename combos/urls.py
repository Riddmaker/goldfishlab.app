"""Combo URLs.

One route, and it is a POST. Looking at combos is part of the deck page;
*fetching* them is the only thing in this application that talks to Commander
Spellbook, so it gets its own address and its own verb.
"""

from django.urls import path

from combos import views

app_name = "combos"

urlpatterns = [
    path("<uuid:pk>/refresh/", views.RefreshCombosView.as_view(), name="refresh"),
]
