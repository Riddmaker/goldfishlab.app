"""B-10: paid plans may give a deck its own look, up to ten looks in all.

The free and guest plans carry no `looks` key: one look, for every deck
(`looks.services.LOOKS_DEFAULT`).
"""

from django.db import migrations

PAID = ("koi", "kraken")


def add(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for plan in Plan.objects.filter(slug__in=PAID):
        plan.features = {**plan.features, "looks": 10}
        plan.save(update_fields=["features"])


def remove(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for plan in Plan.objects.filter(slug__in=PAID):
        plan.features = {k: v for k, v in plan.features.items() if k != "looks"}
        plan.save(update_fields=["features"])


class Migration(migrations.Migration):
    dependencies = [("billing", "0010_subscription_cancel_at")]
    operations = [migrations.RunPython(add, remove)]
