"""Rate limiting: where the client's address comes from, and what a refusal looks like.

Two libraries count requests in this application and they do not know about
each other. allauth limits login, signup and password reset out of its own box
(`ACCOUNT_RATE_LIMITS`, see base.py); django-ratelimit limits the two endpoints
that cost real money, the imports and the simulation enqueue. Both read
`django.core.cache`, so both are only as shared as that cache is - which is why
base.py points it at Redis and why trap 40 exists.

What is left for this module is the two things they disagree about: which
address counts as the client, and what the person sees when they are refused.
"""

from django.conf import settings
from django.shortcuts import render
from django_ratelimit.exceptions import Ratelimited

# 429 is not in `http.HTTPStatus` as a Django constant anywhere convenient, and
# writing the number once beats importing `HTTPStatus` in four modules.
TOO_MANY_REQUESTS = 429


def client_ip(request):
    """The address to count a request against, behind one reverse proxy.

    Wired in as `RATELIMIT_IP_META_KEY`. django-ratelimit's own default is
    `REMOTE_ADDR`, which in production is the Cloudflare Tunnel and therefore the
    same value for every visitor on earth: the first few people through the
    door would spend the whole limit for everybody, and the symptom would be a
    site that refuses strangers at random.

    **The RIGHT-hand end of X-Forwarded-For, not the left.** The header is a
    list that each proxy appends to, so it reads `client, proxy1, proxy2`. The
    leftmost entry is the one everybody reaches for and it is the one the
    *client* can write: anybody can send `X-Forwarded-For: 1.2.3.4` and get a
    fresh bucket per request, which turns the rate limiter off for exactly the
    person it exists to stop. The rightmost entry is the one the last proxy
    added, and a proxy writes the address that actually connected to it.

    More precisely, the entry `settings.TRUSTED_PROXY_COUNT` places from the
    right: one in production. The only way in is the Cloudflare Tunnel
    (Jelastic's shared load balancer is switched off for every node), and
    Cloudflare *appends* the visitor's address to whatever X-Forwarded-For the
    visitor sent; cloudflared passes the header on unchanged (cloudflare/
    cloudflared#1426 documents the append). So the right-hand end is the
    address that reached Cloudflare. Another proxy in front would raise the
    number by one - deliberately, with the hops written down, never by
    trusting the whole list. allauth reads the **same** setting for its own
    limits (`ALLAUTH_TRUSTED_PROXY_COUNT`, see base.py and trap 47), so the two
    limiters cannot disagree about who the client is.

    With no proxy at all - development, docker compose, the test suite - there
    is no header and `REMOTE_ADDR` is the client. A header with fewer entries
    than there are trusted proxies did not come through all of them, so it is
    not believed either.
    """
    count = getattr(settings, "TRUSTED_PROXY_COUNT", 1)
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if count > 0 and forwarded:
        entries = [entry.strip() for entry in forwarded.split(",")]
        if len(entries) >= count and entries[-count]:
            return entries[-count]
    return request.META.get("REMOTE_ADDR", "")


def too_many_requests(request, exception=None):
    """The page both limiters end on.

    allauth finds `429.html` by itself (`allauth.core.internal.ratelimit`
    renders it by name). django-ratelimit instead raises `Ratelimited`, a
    subclass of `PermissionDenied`, so it arrives as a 403 and is routed here
    by `handler403` in the root URLconf.

    Answering 429 rather than 403 is not cosmetic. 403 says "you may not";
    429 says "not so fast", and only one of those tells a person that waiting
    will work.
    """
    return render(request, "429.html", status=TOO_MANY_REQUESTS)


def permission_denied(request, exception=None):
    """`handler403`, which has to tell two different refusals apart.

    django-ratelimit raises `Ratelimited`, and `Ratelimited` subclasses
    `PermissionDenied` - so without this, being too quick and being logged in
    as the wrong person produce the identical page. They are not the identical
    situation: one of them goes away on its own.

    Every other `PermissionDenied` in the application keeps Django's own 403.
    """
    if isinstance(exception, Ratelimited):
        return too_many_requests(request, exception)
    return render(request, "403.html", status=403)
