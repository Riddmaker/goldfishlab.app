"""B-14: decks read from photos a month.

Five on the free plan, Koi 20, Kraken 50; none for a guest (sign up first).
Each run is up to ten Mistral calls with an image, so unlike the card insights
the paid plans have a ceiling too. A data edit changes it later, not a deploy.
"""
from django.db import migrations, models

PHOTO_IMPORTS_PER_MONTH = {"free": 5, "koi": 20, "kraken": 50, "guest": 0}


def set_allowance(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for slug, allowance in PHOTO_IMPORTS_PER_MONTH.items():
        Plan.objects.filter(slug=slug).update(max_photo_imports_per_month=allowance)


def clear_allowance(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.update(max_photo_imports_per_month=None)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0012_plan_card_insights'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='max_photo_imports_per_month',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='usagerecord',
            name='metric',
            field=models.CharField(choices=[('runs_started', 'Runs started'), ('games_simulated', 'Games simulated'), ('imports', 'Imports'), ('decks_created', 'Decks created'), ('summaries_written', 'Summaries written'), ('card_insights', 'Card insights written'), ('photo_imports', 'Decks read from photos')], max_length=32),
        ),
        migrations.RunPython(set_allowance, clear_allowance),
    ]
