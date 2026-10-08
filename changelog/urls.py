"""Changelog URLs (C7).

Two lists, mounted in `goldfishlab.urls`: the page and its feed carry their
language in the address (`i18n_patterns`); the switches keep one address, so
a link in a mail never moves.
"""

from django.urls import path

from changelog import views

page_patterns = [
    path("changelog/", views.ChangelogView.as_view(), name="changelog"),
    path("changelog/feed/", views.ChangelogFeed(), name="changelog_feed"),
]

switch_patterns = [
    path("changelog/subscribe/", views.SubscribeView.as_view(), name="changelog_subscribe"),
    path("changelog/unsubscribe/", views.UnsubscribeView.as_view(),
         name="changelog_unsubscribe_account"),
    path("changelog/unsubscribe/<str:token>/", views.TokenUnsubscribeView.as_view(),
         name="changelog_unsubscribe"),
]
