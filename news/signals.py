"""A wish to get the news by mail, made before signing up or in - the same two
moments as the changelog's (`changelog.signals`)."""

from allauth.account.signals import user_signed_up
from django.contrib import messages
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils.translation import gettext

from core import mailing
from news.services import LIST


@receiver(user_signed_up)
def subscribe_on_sign_up(sender, request, user, **kwargs):
    if request is not None and LIST.wanted(request) and mailing.can_subscribe(user):
        LIST.subscribe(user)


@receiver(user_logged_in)
def subscribe_on_sign_in(sender, request, user, **kwargs):
    if request is None or not LIST.take(request) or not mailing.can_subscribe(user):
        return
    LIST.subscribe(user)
    messages.success(request, gettext(
        "You're subscribed: the Commander news comes by mail once a week."))
