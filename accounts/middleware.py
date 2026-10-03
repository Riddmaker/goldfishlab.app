"""A signed-in person's own language comes first (phase 12, Q1).

Django's `LocaleMiddleware` runs before authentication and knows only the
cookie and the browser. This runs after it and puts the language saved on the
account on top, so a person who picked German on their laptop gets German on
their phone as well. A code that is not switched on (any more) is ignored and
the cookie and browser decide, as for anybody else.
"""

from django.conf import settings
from django.utils import translation


class AccountLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        language = getattr(user, "language", "") if user and user.is_authenticated else ""
        if language and language in dict(settings.LANGUAGES):
            translation.activate(language)
            request.LANGUAGE_CODE = language
        return self.get_response(request)
