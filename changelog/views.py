"""The changelog's page and feed, and the switches of its mail (C7).

The page and the feed carry their language in the address, like every public
page (P10). The switches do not: they are forms and mail links, and a link in
a mail must not move (`goldfishlab.urls`). The page in a mail link's language
comes from the account it was made for.
"""

from datetime import datetime
from itertools import groupby

from django.conf import settings
from django.contrib import messages
from django.contrib.syndication.views import Feed
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View

from changelog import entries, services
from core import seo
from guests import services as guest_services


class ChangelogView(TemplateView):
    """/changelog/: every entry, newest first, grouped by month. The feed is
    offered first; the mail below it."""

    template_name = "changelog/page.html"

    def get_context_data(self, **kwargs):
        published = entries.published()
        months = [(month, list(group)) for month, group in
                  groupby(published, key=lambda entry: entry.day.replace(day=1))]
        user = self.request.user
        return {
            **super().get_context_data(**kwargs),
            "months": months,
            "feed_url": seo.absolute(self.request, reverse("changelog_feed")),
            "mail_on": services.mail_on(),
            "can_subscribe": services.can_subscribe(user),
            "subscribed": services.can_subscribe(user) and user.changelog_mail,
        }


class ChangelogFeed(Feed):
    """/changelog/feed/: RSS 2.0, in the language of its address. Each item
    links its entry's anchor on the page, which is also its id."""

    title = _("Goldfish Lab: what's new")
    description = _("What changed on Goldfish Lab, newest first.")

    def link(self):
        return reverse("changelog")

    def items(self):
        return entries.published()

    def item_title(self, item):
        return str(item.title)

    def item_description(self, item):
        return str(item.text)

    def item_link(self, item):
        return f"{reverse('changelog')}#{item.slug}"

    def item_pubdate(self, item):
        return timezone.make_aware(datetime.combine(item.day, datetime.min.time()))


def _back(request, fallback: str) -> str:
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                       require_https=request.is_secure()):
        return target
    return fallback


class SubscribeView(View):
    """"Subscribe by email". An account subscribes and goes back; anybody
    else is sent to make one, with the wish kept (`changelog.services`)."""

    def post(self, request):
        if not services.mail_on():
            raise Http404
        back = _back(request, reverse("changelog"))
        user = request.user
        if services.can_subscribe(user):
            services.subscribe(user)
            messages.success(request, gettext(
                "You're subscribed: the changelog comes by mail once a month, "
                "when there is something new."))
            return redirect(back)
        services.want(request)
        if guest_services.is_guest(user):
            return redirect("guests:save")
        return redirect(f"{reverse('account_signup')}?{urlencode({'next': back})}")


class UnsubscribeView(View):
    """The switch on /changelog/ and on "Your plan", for a signed-in account."""

    def post(self, request):
        if services.can_subscribe(request.user):
            services.unsubscribe(request.user)
            messages.success(request, gettext(
                "Unsubscribed. No more changelog mails."))
        return redirect(_back(request, reverse("changelog")))


@method_decorator(csrf_exempt, name="dispatch")
class TokenUnsubscribeView(View):
    """The link in every mail. Opening it shows one button and changes
    nothing: mail services open every link in a mail to scan it. Pressing
    the button - or the mail program's own "Unsubscribe", which posts here
    without a page (RFC 8058, `List-Unsubscribe-Post`) - switches it off.

    No CSRF token, because the mail program has none: the signed token is
    the proof, and all it can ever do is switch a mail off."""

    template_name = "changelog/unsubscribe.html"

    def dispatch(self, request, token):
        self.account = services.user_for(token)
        if self.account is None:
            raise Http404
        # The account's own language; without one, the visitor's.
        own = self.account.language in dict(settings.LANGUAGES)
        with translation.override(self.account.language if own else translation.get_language()):
            return super().dispatch(request, token)

    def get(self, request, token):
        return render(request, self.template_name,
                      {"account": self.account, "done": False})

    def post(self, request, token):
        services.unsubscribe(self.account)
        return render(request, self.template_name,
                      {"account": self.account, "done": True})

