"""Deployment checks that turn a silent production failure into a refusal.

Django's `manage.py check --deploy` already covers the settings it knows about.
These cover the ones it cannot: configuration that is only wrong in the
combination this project happens to use.

Every check here exists because the failure it catches is **invisible until a
stranger hits it**. A check that fires in development would be noticed anyway
and does not need to be here.
"""

from django.conf import settings
from django.core.checks import Error, Warning, register

#: Backends that send nothing anywhere, and are therefore fine without a host.
_HOSTLESS_BACKENDS = (
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
)


@register("email")
def check_email_is_configured(app_configs, **kwargs):
    """Mandatory email verification plus no mail host is a broken signup.

    `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` means allauth sends a
    confirmation during signup. Django's default backend is SMTP to
    localhost:25, which does not exist in the production container - so the
    failure mode is **HTTP 500 on the signup form**, not a degraded experience.

    It is invisible everywhere it would be cheap to notice: development uses
    the console backend, and Django's test runner substitutes locmem, so the
    whole suite passes. The first person to meet it is a real visitor.

    So it is an Error rather than a Warning: `manage.py check` fails, which
    means the container refuses to start rather than starting and being wrong.
    """
    errors = []

    backend = getattr(settings, "EMAIL_BACKEND", "")
    if backend in _HOSTLESS_BACKENDS:
        return errors

    verification = getattr(settings, "ACCOUNT_EMAIL_VERIFICATION", "none")
    if verification == "none":
        return errors

    if not getattr(settings, "EMAIL_HOST", ""):
        errors.append(
            Error(
                "Email verification is mandatory but no EMAIL_HOST is set.",
                hint=(
                    "allauth sends a confirmation message during signup, so with "
                    "no mail host the signup form answers HTTP 500. Set "
                    "DJANGO_EMAIL_HOST (and the user/password beside it), or set "
                    "DJANGO_EMAIL_BACKEND to the console backend if this "
                    "installation is deliberately not sending mail."
                ),
                id="core.E001",
            )
        )

    if getattr(settings, "EMAIL_USE_TLS", False) and getattr(
        settings, "EMAIL_USE_SSL", False
    ):
        errors.append(
            Error(
                "EMAIL_USE_TLS and EMAIL_USE_SSL are both on.",
                hint=(
                    "They are mutually exclusive. STARTTLS on port 587 is "
                    "EMAIL_USE_TLS; implicit TLS on 465 is EMAIL_USE_SSL. "
                    "Django raises at send time, naming neither."
                ),
                id="core.E002",
            )
        )

    return errors


@register("legal")
def check_operator_is_named(app_configs, **kwargs):
    """A privacy policy with no controller in it is not a privacy policy.

    The Swiss DPA (Art. 19) requires the controller's identity and contact
    details, and the UWG (Art. 3 para. 1 lit. s) requires anyone offering
    services online to state a name and a postal and email address. The legal
    pages read all three from settings; blank, they would render a policy that
    names nobody. Production refuses to boot instead.
    """
    if not getattr(settings, "LEGAL_DETAILS_REQUIRED", False):
        return []
    missing = [
        name
        for name in ("LEGAL_OPERATOR_NAME", "LEGAL_OPERATOR_ADDRESS", "LEGAL_CONTACT_EMAIL")
        if not getattr(settings, name, None)
    ]
    if not missing:
        return []
    return [
        Error(
            f"The operator is not named: {', '.join(missing)} blank.",
            hint=(
                "The terms, privacy policy and legal notice name the person who "
                "runs this installation. Set LEGAL_OPERATOR_NAME, "
                "LEGAL_OPERATOR_ADDRESS (comma-separated lines) and "
                "LEGAL_CONTACT_EMAIL on the web node."
            ),
            id="core.E003",
        )
    ]


@register("seo")
def check_site_url_is_set(app_configs, **kwargs):
    """Production names its one address (P3).

    Without SITE_URL the canonical links, Open Graph URLs and the sitemap name
    whatever host the request came in on - www., or the hoster's own domain -
    and search engines index the same page twice. Nothing breaks, which is why
    this warns rather than refusing to boot.
    """
    if settings.DEBUG or settings.SITE_URL:
        return []
    return [
        Warning(
            "SITE_URL is not set.",
            hint=(
                "Set SITE_URL=https://goldfishlab.app (no trailing slash) on the "
                "web node, so canonical links and the sitemap name one address."
            ),
            id="core.W001",
        )
    ]
