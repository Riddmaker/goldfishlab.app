"""Is this installation ready to take money? (B-18)

`manage.py stripe_check` asks Stripe, read-only, whether what the owner set up
in the Dashboard matches what the code expects, and prints one line per
finding. It never prints a key or a secret: only whether one is set and
whether the key is a live or a test key.

What it checks, and why each one would otherwise go unnoticed:

- the key and the webhook secret are set (without the key the plans page
  simply offers nothing, which looks like "paid plans start soon");
- each paid plan's Stripe prices exist, are active, recur monthly or yearly
  as the field says, cost in francs what the plan shows (`price_chf_cents`,
  `annual_prices`), and, with `LOCAL_PRICES`, carry the same round amount in
  every other currency the plan lists;
- each price's product has a tax code (Managed Payments refuses a product
  without one) and is in the same mode as the key;
- a webhook endpoint points at this site's `/billing/webhook/`, is enabled
  and listens to every event `services.HANDLED` acts on.

A finding is "ok", "warn" (works, but worth a look) or "fail" (a payment
would go wrong). `findings()` takes no arguments and talks to Stripe only
through `stripe_api`, which is what the tests replace.
"""

from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse

from billing import services, stripe_api
from billing.models import Plan

INTERVALS = {"month": "stripe_price_id", "year": "stripe_annual_price_id"}


@dataclass(frozen=True)
class Finding:
    level: str  # "ok", "warn" or "fail"
    text: str


def _ok(text):
    return Finding("ok", text)


def _warn(text):
    return Finding("warn", text)


def _fail(text):
    return Finding("fail", text)


def findings() -> list[Finding]:
    found = [_setup()]
    if not stripe_api.is_configured():
        return found
    found.append(_ok("webhook secret set") if settings.STRIPE_WEBHOOK_SECRET
                 else _fail("STRIPE_WEBHOOK_SECRET is not set: every webhook will be refused"))
    mode = stripe_api.mode()
    found.append(_ok(f"key mode: {mode}") if mode != "unknown"
                 else _warn("key mode: unknown prefix"))
    found.append(_ok(f"Managed Payments: {'on' if settings.STRIPE_MANAGED_PAYMENTS else 'off'}"))
    plans = Plan.objects.filter(is_active=True, is_default=False).order_by("price_chf_cents")
    if not plans:
        found.append(_fail("no paid plan is active"))
    for plan in plans:
        found.extend(_plan(plan, mode))
    found.extend(_webhook())
    return found


def _setup() -> Finding:
    if stripe_api.is_configured():
        return _ok("STRIPE_SECRET_KEY set")
    return _fail("STRIPE_SECRET_KEY is not set: the plans page offers nothing to buy")


def _plan(plan: Plan, mode: str) -> list[Finding]:
    found = []
    for interval, field in INTERVALS.items():
        price_id = getattr(plan, field)
        cents = plan.price_chf_cents if interval == "month" else plan.annual_cents("chf")
        name = f"{plan.slug} ({interval})"
        if not price_id:
            if interval == "month" or cents:
                found.append(_fail(f"{name}: no Stripe price id in the admin ({field})"))
            continue
        try:
            price = stripe_api.price(price_id)
        except stripe_api.StripeError as error:
            found.append(_fail(f"{name}: Stripe does not know {price_id} ({type(error).__name__})"))
            continue
        found.extend(_price(name, plan, interval, cents, price, mode))
    return found


def _price(name, plan, interval, cents, price, mode) -> list[Finding]:
    problems = []
    if not price.get("active"):
        problems.append(_fail(f"{name}: the price is archived"))
    recurring = (price.get("recurring") or {}).get("interval")
    if recurring != interval:
        problems.append(_fail(
            f"{name}: Stripe bills every {recurring or 'once'}, not every {interval}"))
    if price.get("currency") != "chf" or price.get("unit_amount") != cents:
        problems.append(_fail(
            f"{name}: Stripe charges {price.get('unit_amount')} {price.get('currency')}, "
            f"the plan shows {cents} chf"))
    if settings.LOCAL_PRICES:
        amounts = plan.prices if interval == "month" else plan.annual_prices
        options = price.get("currency_options") or {}
        for currency, amount in sorted((amounts or {}).items()):
            if currency == "chf":
                continue
            if (options.get(currency) or {}).get("unit_amount") != amount:
                problems.append(_fail(f"{name}: no {currency} option of {amount} on the price"))
    livemode = price.get("livemode")
    if livemode is not None and mode != "unknown" and livemode != (mode == "live"):
        problems.append(_fail(f"{name}: the price is not in {mode} mode"))
    product = price.get("product") or {}
    if isinstance(product, dict):
        if not product.get("active", True):
            problems.append(_fail(f"{name}: the product is archived"))
        if not product.get("tax_code"):
            level = _fail if settings.STRIPE_MANAGED_PAYMENTS else _warn
            problems.append(level(
                f"{name}: the product has no tax code (Managed Payments needs one)"))
    return problems or [_ok(f"{name}: {cents / 100:g} chf, {price['id']}")]


def _webhook() -> list[Finding]:
    url = f"{settings.SITE_URL}{reverse('billing:webhook')}"
    try:
        endpoints = stripe_api.webhook_endpoints()
    except stripe_api.StripeError as error:
        return [_fail(f"webhook endpoints could not be listed ({type(error).__name__})")]
    ours = [endpoint for endpoint in endpoints if endpoint.get("url") == url]
    if not ours:
        return [_fail(f"no webhook endpoint for {url}")]
    found = []
    for endpoint in ours:
        events = set(endpoint.get("enabled_events") or [])
        missing = [] if "*" in events else sorted(set(services.HANDLED) - events)
        if endpoint.get("status") != "enabled":
            found.append(_fail(f"webhook {url} is {endpoint.get('status')}"))
        elif missing:
            found.append(_fail(f"webhook {url} does not send: {', '.join(missing)}"))
        else:
            found.append(_ok(f"webhook {url} sends all {len(services.HANDLED)} events"))
    if len(ours) > 1:
        found.append(_warn(f"{len(ours)} endpoints for {url}: only one secret can verify them"))
    return found
