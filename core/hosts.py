"""One address for every page (search console, 2026-10-12).

`SITE_URL` already makes every canonical link name goldfishlab.app (P3), but
www.goldfishlab.app and the hoster's own domain still answered every page
with a 200. Google crawled each page twice and listed the www copies as
duplicates without a canonical of their own choosing. Now a GET or HEAD on any
other host is sent, permanently, to the same path and query on `SITE_URL`.

Left alone:

* Anything but GET and HEAD. A 301 turns a POST into a GET in most clients,
  so a form or a webhook would lose its body; it is answered where it was sent.
* `/healthz/`, which the hoster may ask on an internal address.
* Everything, while `SITE_URL` is blank or `SITE_URL_REDIRECT` is off. It is on
  only in production (`settings.prod`), so a development `.env` that names the
  real site never sends localhost there.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponsePermanentRedirect

SAFE_METHODS = ("GET", "HEAD")
EXEMPT_PATHS = ("/healthz/",)


class CanonicalHostMiddleware:
    """First in the stack, so http://www. reaches https://goldfishlab.app in
    one hop rather than two (SecurityMiddleware's own redirect keeps the host)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        target = self._target(request)
        if target:
            return HttpResponsePermanentRedirect(target)
        return self.get_response(request)

    @staticmethod
    def _target(request) -> str | None:
        if not (settings.SITE_URL and settings.SITE_URL_REDIRECT):
            return None
        if request.method not in SAFE_METHODS or request.path in EXEMPT_PATHS:
            return None
        site = urlsplit(settings.SITE_URL)
        # get_host() checks ALLOWED_HOSTS first: a host not on it is still a 400.
        if request.get_host() == site.netloc and request.scheme == site.scheme:
            return None
        return settings.SITE_URL + request.get_full_path()
