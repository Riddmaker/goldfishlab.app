"""P5: the plans become Goldfish, Koi and Kraken (launch plan, decided
2026-10-08).

"Planeswalker" is a Wizards of the Coast trademark in the EU, so the paid
plans are renamed before anything is sold. The rows are renamed in place -
same primary key - so a subscription on one keeps its plan.

- Goldfish (the free plan, slug `free`): 9 turns instead of 6, so a bracket
  check reaches the turns where a deck's plan shows.
- Koi (was Planeswalker): CHF 4 a month or 36 a year, 12 turns.
- Kraken (was Archmage, the lab tier): CHF 9 a month instead of 12, or 84
  a year. Its Stripe price billed 12, so it is cleared: the plan cannot be
  bought at the old price, and a new price is made for it (M5).

The feature flags named things that were never built (API, deck matrix,
saved playtests ...); nothing reads them, and a plan must not seem to sell
them, so they are emptied. Guest is not touched.
"""

from django.db import migrations

CURRENCIES = ("chf", "eur", "usd")


def _each(cents: int) -> dict:
    return {currency: cents for currency in CURRENCIES}


PLANS = {
    "free": {"name": "Goldfish", "max_turns": 9, "features": {}},
    "planeswalker": {
        "slug": "koi", "name": "Koi", "price_chf_cents": 400, "prices": _each(400),
        "annual_prices": _each(3600), "max_turns": 12, "features": {},
    },
    "archmage": {
        "slug": "kraken", "name": "Kraken", "price_chf_cents": 900, "prices": _each(900),
        "annual_prices": _each(8400), "stripe_price_id": "", "max_turns": 15,
        "features": {},
    },
}

OLD = {
    "free": {"name": "Free", "max_turns": 6},
    "koi": {"slug": "planeswalker", "name": "Planeswalker", "max_turns": 10,
            "annual_prices": {}},
    "kraken": {"slug": "archmage", "name": "Archmage", "price_chf_cents": 1200,
               "prices": _each(1200), "max_turns": 15, "annual_prices": {}},
}


def rename(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for slug, values in PLANS.items():
        Plan.objects.filter(slug=slug).update(**values)


def restore(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for slug, values in OLD.items():
        Plan.objects.filter(slug=slug).update(**values)


class Migration(migrations.Migration):
    dependencies = [("billing", "0008_plan_annual")]
    operations = [migrations.RunPython(rename, restore)]
