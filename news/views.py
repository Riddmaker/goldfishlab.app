"""The news page, its feed and the switches of its mail (PR 22).

Built like the changelog's (`changelog.views`): the page and the feed carry
their language in the address; the three switches are the changelog's views
with the news list and its own words.
"""

from datetime import datetime

from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from changelog import views as changelog
from core import mailing, seo
from news import issues
from news.services import LIST


class NewsView(TemplateView):
    """/news/: every week, newest first, each item in the reader's language
    with its links. The label says how it was written and who checked it."""

    template_name = "news/page.html"

    def get_context_data(self, **kwargs):
        user = self.request.user
        return {
            **super().get_context_data(**kwargs),
            "issues": issues.published(),
            "feed_url": seo.absolute(self.request, reverse("news_feed")),
            "mail_on": mailing.mail_on(),
            "can_subscribe": mailing.can_subscribe(user),
            "subscribed": LIST.is_on(user),
        }


class NewsFeed(Feed):
    """/news/feed/: RSS 2.0, one item per piece of news, in the language of
    its address. Kept out of search results by its header, as the
    changelog's feed is."""

    title = _("Goldfish Lab: Commander news")
    description = _("The week's Commander news in a few sentences, with links to the "
                    "articles. Summarised with AI, checked before it is published.")

    def __call__(self, request, *args, **kwargs):
        response = super().__call__(request, *args, **kwargs)
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    def link(self):
        return reverse("news")

    def items(self):
        return [(issue, item) for issue in issues.published() for item in issue.items]

    def item_title(self, item):
        return item[1].headline

    def item_description(self, item):
        sources = ", ".join(link.source for link in item[1].links)
        return f"{item[1].summary} ({gettext('Source')}: {sources})"

    def item_link(self, item):
        return f"{reverse('news')}#{item[1].anchor}"

    def item_pubdate(self, item):
        return timezone.make_aware(datetime.combine(item[0].published, datetime.min.time()))


class SubscribeView(changelog.SubscribeView):
    mail_list = LIST
    page = "news"

    def subscribed(self):
        return gettext("You're subscribed: the Commander news comes by mail once a week.")


class UnsubscribeView(changelog.UnsubscribeView):
    mail_list = LIST
    page = "news"

    def unsubscribed(self):
        return gettext("Unsubscribed. No more news mails.")


class TokenUnsubscribeView(changelog.TokenUnsubscribeView):
    template_name = "news/unsubscribe.html"
    mail_list = LIST
