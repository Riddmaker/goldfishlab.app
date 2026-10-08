"""A wish to get the changelog by mail, made before signing up or in (C7).

`changelog.services` explains why both: the sign-up keeps the wish on the
account whichever browser confirms the address, and the sign-in is where an
existing account says yes and where the page can say so.
"""

from allauth.account.signals import user_signed_up
from django.contrib import messages
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils.translation import gettext

from changelog import services


@receiver(user_signed_up)
def subscribe_on_sign_up(sender, request, user, **kwargs):
    if request is not None and services.wanted(request) and services.can_subscribe(user):
        services.subscribe(user)


@receiver(user_logged_in)
def subscribe_on_sign_in(sender, request, user, **kwargs):
    if request is None or not services.take(request) or not services.can_subscribe(user):
        return
    services.subscribe(user)
    messages.success(request, gettext(
        "You're subscribed: the changelog comes by mail once a month, "
        "when there is something new."))
