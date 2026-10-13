"""allauth's account adapter, with the one thing this site adds (P20 F3)."""

from allauth.account.adapter import DefaultAccountAdapter

from accounts import language


class AccountAdapter(DefaultAccountAdapter):
    """A new account keeps the language it was made in (E1.7)."""

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        language.remember(user)
        if commit:
            user.save()
        return user
