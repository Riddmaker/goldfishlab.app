"""Production settings - Infomaniak Jelastic.

The hardening pass is Phase 8 and is done. What is NOT here is as deliberate as
what is: the Content-Security-Policy, the cache the rate limiters count in, and
the upload ceilings all live in `base.py`, because a security control that is
only switched on in production is a security control nobody has ever run. Every
one of them is exercised by the test suite and by `docker compose up`.

What is left here is the handful of things that are meaningless without TLS.
"""

from .base import *  # noqa: F403

DEBUG = False

# The legal pages name the operator; production refuses to boot without them
# (core.E003) rather than serving a privacy policy with no controller in it.
LEGAL_DETAILS_REQUIRED = True

# Cloudflare sets X-Forwarded-Proto to the scheme the visitor used and
# overwrites any value the visitor sent; the tunnel is the only way in, so the
# header cannot come from anywhere else. It survives the tunnel (confirmed at
# the first deploy, 2026-09-28; cloudflared#1245 once reported it missing). If
# every page ever answers with a redirect to itself, it stopped arriving, and a
# Cloudflare request header transform rule setting it to "https" is the fix -
# not switching the redirect off.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
LANGUAGE_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])  # noqa: F405

# --- HSTS -------------------------------------------------------------------
# One year, and subdomains included. This is the one setting in this file that
# is hard to undo: a browser that has seen the header refuses plain HTTP to
# this host for a year, and there is no way to call it back. That is the point
# of it, and it is also why the value is an environment variable - a first
# deploy onto a hostname whose certificate is not settled yet should be able to
# start at 0, prove the certificate, and then turn it up. (goldfishlab.app gains
# nothing from 0: the whole .app TLD is on the browsers' HSTS preload list, so
# browsers use HTTPS there no matter what this header says.)
#
# Preload is left OFF and should stay off until somebody has decided to submit
# the domain to the browser preload list, which is a one-way door on a much
# longer timescale than a year.
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=31536000)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

# --- Headers Django already sets, restated ----------------------------------
# Every one of these is Django 5.2's own default, so this block changes
# nothing today. It is here because they are security controls and a default
# is somebody else's decision: if a future Django relaxes one, this file is
# where it stays decided, and tests/test_security_headers.py asserts the
# response rather than the setting.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --- Email ------------------------------------------------------------------
# NOT optional, and the reason is `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` in
# base.py: allauth sends a confirmation message during signup, so if mail does
# not work then **signup does not work**.
#
# Django's default backend is SMTP to localhost:25, which does not exist in
# this container - so leaving this unset does not degrade gracefully, it raises
# ConnectionRefusedError and answers HTTP 500 on the signup form. Nothing
# catches it and nothing warns about it: development uses the console backend
# and Django's test runner substitutes locmem, so every test passes and the
# first person to ever hit it is a real stranger on the public site. See
# trap 43.
#
# `check_email_is_configured` in core/checks.py turns that into a refusal to
# boot rather than a 500 an hour later.
# Django's SMTP backend with a Message-ID on the From domain instead of the
# node's host name (core/mail.py, phase 12 J30).
EMAIL_BACKEND = env(  # noqa: F405
    "DJANGO_EMAIL_BACKEND", default="core.mail.EmailBackend"
)
EMAIL_HOST = env("DJANGO_EMAIL_HOST", default="")  # noqa: F405
EMAIL_PORT = env.int("DJANGO_EMAIL_PORT", default=587)  # noqa: F405
EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", default="")  # noqa: F405
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", default="")  # noqa: F405
# STARTTLS on 587 is what Infomaniak's SMTP wants. Implicit TLS on 465 is the
# other convention; they are mutually exclusive and setting both fails to send
# with an error that names neither.
EMAIL_USE_TLS = env.bool("DJANGO_EMAIL_USE_TLS", default=True)  # noqa: F405
EMAIL_USE_SSL = env.bool("DJANGO_EMAIL_USE_SSL", default=False)  # noqa: F405
EMAIL_TIMEOUT = 10

# The address confirmations and password resets come FROM. An address on the
# sending domain, or SPF/DKIM will not line up and the mail goes to spam -
# which for a mandatory confirmation is indistinguishable from being broken.
# noreply@ is not a mailbox; replies go to EMAIL_REPLY_TO below.
DEFAULT_FROM_EMAIL = env(  # noqa: F405
    "DJANGO_DEFAULT_FROM_EMAIL", default="Goldfish Lab <noreply@goldfishlab.app>"
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# noreply@ is no mailbox: a reply goes to one that is read (core/mail.py, P6).
EMAIL_REPLY_TO = env(  # noqa: F405
    "DJANGO_EMAIL_REPLY_TO", default="Goldfish Lab <hello@goldfishlab.app>"
)

# --- Sentry -----------------------------------------------------------------
# Inert unless a DSN is set. `sentry_sdk.init` with an empty DSN is a no-op by
# design, but calling it anyway would still install the integrations, so the
# import is inside the branch: an installation with no DSN does not pay for
# the instrumentation either.
#
# `send_default_pii` stays False. This application holds other people's
# decks, and the one thing a crash report must not do is carry
# them out of the country the privacy policy promises they stay in.
SENTRY_DSN = env("SENTRY_DSN", default="")  # noqa: F405
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        send_default_pii=False,
        # A share of requests, not all of them: tracing every simulation
        # enqueue on a 128 MiB cloudlet buys noise and costs memory.
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.05),  # noqa: F405
        environment=env("SENTRY_ENVIRONMENT", default="production"),  # noqa: F405
    )

# --- Logging ----------------------------------------------------------------
# JSON in production, one object per line, because the thing reading it is a
# log aggregator and not a person. The human-readable formatter stays the
# default everywhere else - see base.py - since the thing reading it there IS a
# person, watching `docker compose logs`.
LOGGING["formatters"]["json"] = {  # noqa: F405
    "()": "core.logging.JSONFormatter",
}
LOGGING["handlers"]["console"]["formatter"] = "json"  # noqa: F405
