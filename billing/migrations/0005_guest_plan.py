"""The plan a guest runs on (phase 9 G): trying the site without an account.

Stricter than Free, because nobody had to give an address to get it: one deck,
small runs on the short queue, one at a time. **Inactive**, so it is never on
the plans page and cannot be bought - `plan_for` reads a subscription's plan
whatever its flag, so the guest still gets these limits.

Limits live in the database, so changing a number is a data edit, not a deploy.
"""

from django.db import migrations

GUEST = {
    "slug": "guest",
    "name": "Guest",
    "price_chf_cents": 0,
    "is_default": False,
    "is_active": False,
    "max_decks": 1,
    "max_games_per_run": 2_000,
    "max_turns": 6,
    "max_runs_per_month": 10,
    "max_concurrent_runs": 1,
    "max_imports_per_month": 10,
    "features": {},
}


def seed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.update_or_create(slug=GUEST["slug"], defaults=GUEST)


def unseed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(slug=GUEST["slug"]).delete()


class Migration(migrations.Migration):
    dependencies = [("billing", "0004_subscription_last_event_at")]
    operations = [migrations.RunPython(seed, unseed)]
