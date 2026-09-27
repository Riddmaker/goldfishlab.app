"""A failed combo lookup no longer re-dates the answer it failed to replace.

`fetched_at` was `auto_now`, so saving a failure moved it to "now" and the old
list of combos read as checked today (2026-09-25 review, B20). It now moves only
on success, and `attempted_at` - which the cooldown counts from - moves on every
attempt. Existing rows keep their date as both: every lookup stored so far that
has entries got them from a success at or before that moment.
"""

import django.utils.timezone
from django.db import migrations, models


def copy_dates(apps, schema_editor):
    ComboLookup = apps.get_model("combos", "ComboLookup")
    ComboLookup.objects.update(attempted_at=models.F("fetched_at"))


class Migration(migrations.Migration):
    dependencies = [("combos", "0002_combomeasurement")]

    operations = [
        migrations.AddField(
            model_name="combolookup",
            name="attempted_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="combolookup",
            name="fetched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(copy_dates, migrations.RunPython.noop),
    ]
