"""Whether this visitor asked for the changelog by mail before signing up (C7),
so the sign-up page can say what will happen."""

from django.utils.functional import SimpleLazyObject

from changelog import services


def mail_wanted(request):
    return {"changelog_wanted": SimpleLazyObject(lambda: services.wanted(request))}
