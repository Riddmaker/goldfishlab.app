"""Phase 11 G (K13): each plan's price per currency, the same round number in each.

Planeswalker 4 and Archmage 12, in francs, euros and dollars alike; the free
and guest plans cost nothing in any currency. Filled from `price_chf_cents`, so
a plan whose franc price was edited in the admin keeps that number.
"""

from django.db import migrations, models

CURRENCIES = ("chf", "eur", "usd")


def fill(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for plan in Plan.objects.all():
        plan.prices = ({currency: plan.price_chf_cents for currency in CURRENCIES}
                       if plan.price_chf_cents else {})
        plan.save(update_fields=["prices"])


class Migration(migrations.Migration):
    dependencies = [("billing", "0005_guest_plan")]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="prices",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
