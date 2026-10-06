"""Counting new accounts (P2).

allauth sends `user_signed_up` for the sign-up form and for a guest who saves
their deck (guests.views.SaveDeckView), and never for a guest itself, which
is made without allauth.
"""

from allauth.account.signals import user_signed_up
from django.dispatch import receiver

from metrics import counts


@receiver(user_signed_up)
def count_signup(sender, request, user, **kwargs):
    counts.add(counts.Name.SIGNUP)
