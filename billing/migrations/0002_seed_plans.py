"""Seed the pricing tiers.

Limits live in the database, not in settings.py, so changing one is a data
edit rather than a deploy. The `free` plan must always exist: every user is
attached to it at signup.

Pricing rationale (see instructions.md): the honest lever is TURNS SIMULATED,
not games per run. 10k games already gives +/-0.5pp at 95% confidence, so
selling iterations sells a number that does not improve the answer. Simulating
to turn 15 instead of turn 6 costs real CPU and genuinely changes what you
learn.
"""

from django.db import migrations

PLANS = [
    {
        "slug": "free",
        "name": "Free",
        "price_chf_cents": 0,
        "is_default": True,
        "max_decks": 3,
        "max_games_per_run": 10_000,
        "max_turns": 6,
        "max_runs_per_month": 20,
        "max_concurrent_runs": 1,
        "features": {},
    },
    {
        "slug": "planeswalker",
        "name": "Planeswalker",
        "price_chf_cents": 400,
        "is_default": False,
        "max_decks": None,
        "max_games_per_run": 100_000,
        "max_turns": 10,
        "max_runs_per_month": 300,
        "max_concurrent_runs": 3,
        "features": {"run_history": True, "export": True, "saved_playtests": True},
    },
    {
        "slug": "archmage",
        "name": "Archmage",
        "price_chf_cents": 1200,
        "is_default": False,
        "max_decks": None,
        "max_games_per_run": 1_000_000,
        "max_turns": 15,
        "max_runs_per_month": None,
        "max_concurrent_runs": 5,
        "features": {
            "run_history": True,
            "export": True,
            "saved_playtests": True,
            "priority_queue": True,
            "deck_matrix": True,
            "api": True,
            "shareable_playtests": True,
        },
    },
]


def seed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for spec in PLANS:
        Plan.objects.update_or_create(slug=spec["slug"], defaults=spec)


def unseed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(slug__in=[p["slug"] for p in PLANS]).delete()


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
