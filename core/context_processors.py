"""Template context every page can use."""

from django.conf import settings

from core import seo as search


def operator(request):
    """Who runs this installation, for the legal pages and the footer.

    Read from settings rather than written into the templates so a private
    person's home address lives in the deployment's environment, not in the
    repository. Blank in development; `core.E003` refuses to boot production
    while any part is missing.
    """
    return {
        "operator": {
            "name": settings.LEGAL_OPERATOR_NAME,
            "address": settings.LEGAL_OPERATOR_ADDRESS,
            "email": settings.LEGAL_CONTACT_EMAIL,
        }
    }


def payments(request):
    """Who sells a paid plan: us through Stripe, or Stripe's "Sold through Link".

    With Stripe Managed Payments on, Link is the merchant of record - the
    seller the customer contracts with for the payment, who issues receipts
    and owes the VAT. The terms, the privacy policy and the plans page all say
    so, and must never say it while the setting is off.
    """
    return {"sold_through_link": settings.STRIPE_MANAGED_PAYMENTS,
            "local_prices": settings.LOCAL_PRICES}


def source_code(request):
    """Where the code lives, and where to report a problem with it.

    The code is AGPL-3.0, which asks a website running it to offer its users
    the source. The issue tracker doubles as the feedback channel; the legal
    notice keeps the email address for anyone without a GitHub account.
    """
    url = settings.SOURCE_CODE_URL
    return {
        "source_code_url": url,
        "report_url": f"{url}/issues/new/choose" if url else "",
    }


def dev_tools(request):
    """Pages that exist for development only, such as the styleguide.

    Not Django's own `debug` context variable: that also needs the visitor in
    INTERNAL_IPS, which the browser behind docker compose never is.
    """
    return {"show_dev_tools": settings.DEBUG}


def languages(request):
    """The footer switcher's languages, each in its own name (phase 12).

    Not `{% get_available_languages %}`: that runs every name through the
    catalogue of the page's language, so a German page offered "Englisch",
    which a person looking for English does not find.
    """
    return {"site_languages": [(code, settings.LANGUAGE_NAMES[code])
                               for code, _ in settings.LANGUAGES]}


def seo(request):
    """Canonical link, robots and Open Graph tags for base.html (P3, core.seo)."""
    return search.context(request)
