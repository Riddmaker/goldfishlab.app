"""Which language a page is in (phase 12 Q1, P10).

First match wins:

1. The language in the address. Only the public pages have one
   (`goldfishlab.urls`, P10): /de/pricing/ is German for everybody - a search
   engine, a link somebody followed, a signed-in person who picked French.
2. A signed-in person's own choice, saved on the account, so a person who
   picked German on their laptop gets German on their phone as well.
3. The cookie the footer switcher sets, then the browser.
4. English.

A public page's English address (/pricing/, no language in it) is also the
one to pass on and the `x-default` of its hreflang links: a person whose
language by 2. or 3. is another one is sent on to the address in theirs. A
search engine sends no Accept-Language and stays. Only GET and HEAD are sent
on; anything else is answered in English where it was asked.

`LanguageMiddleware` replaces Django's `LocaleMiddleware`, which runs before
authentication and so knows only 1. and 3. - and which, once any address
carries its language, puts every address without one into English, the
app's pages included. `AccountLanguageMiddleware` runs after authentication
and adds 2. and the sending on. A code that is not switched on (any more) is
ignored and the next rule decides.
"""

from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.shortcuts import redirect
from django.urls import translate_url
from django.utils import translation
from django.utils.cache import patch_vary_headers

SAFE_METHODS = ("GET", "HEAD")


def localized(path: str, language: str) -> str | None:
    """`path`, an address without a language, as the address in `language` -
    or None if it is not one of the public pages, or `language` is English.
    A query string is kept. An address that misses only its final slash
    counts, so CommonMiddleware can still add it."""
    if language == settings.LANGUAGE_CODE:
        return None
    # translate_url resolves `path` in the active language, and an address
    # without a language resolves to a public page in English only.
    with translation.override(settings.LANGUAGE_CODE):
        for candidate in (path, _with_slash(path)):
            if candidate and (translated := translate_url(candidate, language)) != candidate:
                return translated
    return None


def _with_slash(path: str) -> str | None:
    address, mark, query = path.partition("?")
    if not settings.APPEND_SLASH or address.endswith("/"):
        return None
    return f"{address}/{mark}{query}"


class LanguageMiddleware(LocaleMiddleware):
    """Django's, except for an address without a language: an app page is in
    the cookie's or the browser's language, as before P10, and a public page
    in English until `AccountLanguageMiddleware` decides where to send the
    person. `request.preferred_language` keeps what the cookie or the browser
    asked for."""

    def process_request(self, request):
        language = translation.get_language_from_path(request.path_info)
        if not language:
            preferred = translation.get_language_from_request(request)
            request.preferred_language = preferred
            language = (settings.LANGUAGE_CODE if localized(request.path_info, preferred)
                        else preferred)
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()


class AccountLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if translation.get_language_from_path(request.path_info):
            return self.get_response(request)  # the address decides
        own = self.account_language(request)
        wanted = own or getattr(request, "preferred_language", settings.LANGUAGE_CODE)
        target = localized(request.get_full_path(), wanted)
        if target and request.method in SAFE_METHODS:
            response = redirect(target)
            # Who is sent where depends on both; a cache must not hand one
            # person's redirect to the next.
            patch_vary_headers(response, ("Accept-Language", "Cookie"))
            return response
        if own and not target:
            translation.activate(own)
            request.LANGUAGE_CODE = own
        return self.get_response(request)

    @staticmethod
    def account_language(request) -> str:
        user = getattr(request, "user", None)
        language = getattr(user, "language", "") if user and user.is_authenticated else ""
        return language if language in dict(settings.LANGUAGES) else ""
