"""Give the free plan a monthly import allowance.

`IMPORTS` has been a `UsageRecord.Metric` since Phase 1, and `consume()` has
been called on every deck and collection import since. What never existed was a
field to check it against, so `quotas.check()` looked the metric up in
`_LIMIT_FIELDS`, found nothing, and returned unlimited every single time. The
lever was built, wired to the usage table, and connected to nothing.

**Five, not one.** An import is the first thing somebody does, and it is also
the thing they do twice when the first file was the wrong one. A cap that bites
on the second attempt does not sell a plan, it loses a user. Five leaves room
to get it wrong, fix the export and try again.

Limits live in the database rather than in settings, so changing this number is
a data edit and not a deploy.
"""

from django.db import migrations, models

FREE_IMPORTS_PER_MONTH = 5


def set_allowance(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    # Paid plans keep NULL, which is this schema's word for unlimited.
    Plan.objects.filter(slug="free").update(max_imports_per_month=FREE_IMPORTS_PER_MONTH)


def clear_allowance(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.update(max_imports_per_month=None)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_seed_plans"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="max_imports_per_month",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(set_allowance, clear_allowance),
    ]
