"""The one form between a guest and an account: deck name, email, password."""

from allauth.account.forms import SignupForm
from django import forms


class SaveDeckForm(SignupForm):
    """allauth's own sign-up form, plus the name the saved deck gets.

    A subclass rather than a copy, so the address check, the password rules,
    the "that address already has an account" handling and the honeypot are
    allauth's, unchanged.
    """

    deck_name = forms.CharField(max_length=120, label="Deck name")

    #: allauth applies it (`set_form_field_order`); the honeypot, if one is
    #: ever configured, keeps its own place.
    field_order = ["deck_name", "email", "password1"]
