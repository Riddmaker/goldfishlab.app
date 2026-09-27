"""The only module in this application that imports `stripe`.

One import site, for three reasons.

**The application has to boot without a key.** Development, the whole test
suite and every screenshot run have no Stripe credentials and must not need
any. `configure()` is called at the point of use rather than at import, and
`is_configured()` is what every view asks before offering to charge somebody.

**A secret must not be read into a module-level constant.** `settings.STRIPE_*`
comes from the environment; reading it lazily means a key rotated in the
environment takes effect on the next request rather than on the next deploy.

**One place to look when the library changes.** Every signature here was read
off the installed source of `stripe==15.6.1` rather than remembered:
`stripe.checkout.Session.create`, `stripe.billing_portal.Session.create` and
`stripe.WebhookSignature.verify_header(payload, header, secret, tolerance=None)`
- whose `None` default turns the replay window off, which is why it is passed.
Reading the *return type* off the source matters as much as the signature: an
earlier version of this module returned `stripe.Webhook.construct_event`'s
`stripe.Event`, which is not a dict, and every caller treated it as one.

Nothing here knows what a `Plan` is. That is `billing/services.py`.
"""

import json

import stripe
from django.conf import settings

#: Raised by `construct_event` when the signature does not check out. Re-exported
#: so the webhook view can catch it without importing `stripe` itself.
SignatureError = stripe.SignatureVerificationError

#: Any other Stripe-side failure - a network problem, a dead API key, a price
#: that no longer exists. Also re-exported so callers stay library-free.
StripeError = stripe.StripeError


def is_configured() -> bool:
    """Can this installation actually take a payment?

    False in development, in the test suite and in any environment where the
    keys are not set. Every screen that offers to charge somebody asks this
    first and says so plainly when the answer is no - an upgrade button that
    500s is worse than an upgrade button that is not there.
    """
    return bool(getattr(settings, "STRIPE_SECRET_KEY", ""))


def configure() -> None:
    """Point the library at this installation's key. Idempotent."""
    stripe.api_key = settings.STRIPE_SECRET_KEY


def checkout_session(**params):
    """Create a hosted Checkout session. Params are Stripe's, not ours."""
    configure()
    return stripe.checkout.Session.create(**params)


def portal_session(**params):
    """Create a hosted Customer Portal session."""
    configure()
    return stripe.billing_portal.Session.create(**params)


def construct_event(payload: bytes, signature: str | None) -> dict:
    """Verify a webhook's signature and return the event as a plain dict.

    **A plain dict, and that is the whole reason this function exists in this
    shape** (trap 45). `stripe.Webhook.construct_event` returns a
    `stripe.Event`, and in `stripe==15` a `StripeObject` is deliberately *not*
    a `dict`: `.get()` and `.items()` raise `AttributeError`, iterating it
    raises `TypeError`. `billing/services.py` reads events with `.get()` and
    stores `dict(event)`, so every real webhook would have answered HTTP 500
    and Stripe would have retried for three days and then disabled the
    endpoint - while the test suite, which replaced this function with
    `json.loads`, stayed green. So the signature is verified with the
    library's own `WebhookSignature.verify_header` and the body is parsed here,
    which is also what keeps every caller free of the library's types.

    Raises:
        SignatureError: the payload was not signed with our webhook secret,
            or is outside the 300-second replay tolerance. **Never** parse the
            body without this: the endpoint is public and unauthenticated, and
            the signature is the only thing that makes it trustworthy.
        ValueError: the signed body is not JSON.
    """
    # The tolerance has to be passed: `verify_header` defaults it to None, and
    # None switches the replay-window check off entirely. `construct_event`
    # passes its own 300 s default, which is what is restated here.
    stripe.WebhookSignature.verify_header(
        payload,
        signature,
        settings.STRIPE_WEBHOOK_SECRET,
        tolerance=stripe.Webhook.DEFAULT_TOLERANCE,
    )
    text = payload.decode("utf-8") if isinstance(payload, bytes | bytearray) else payload
    return json.loads(text)
