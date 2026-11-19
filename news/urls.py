"""News URLs (PR 22), mounted like the changelog's: the page and its feed in
every language's address, the switches at one address each."""

from django.urls import path

from news import views

page_patterns = [
    path("news/", views.NewsView.as_view(), name="news"),
    path("news/feed/", views.NewsFeed(), name="news_feed"),
]

switch_patterns = [
    path("news/subscribe/", views.SubscribeView.as_view(), name="news_subscribe"),
    path("news/unsubscribe/", views.UnsubscribeView.as_view(),
         name="news_unsubscribe_account"),
    path("news/unsubscribe/<str:token>/", views.TokenUnsubscribeView.as_view(),
         name="news_unsubscribe"),
]
