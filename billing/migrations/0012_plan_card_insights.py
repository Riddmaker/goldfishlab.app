"""B-22: a monthly allowance of AI card insights.

Five on the free plan, enough to try it on the cards that puzzle you; none
for a guest (sign up first); the paid plans unlimited (NULL). A data edit
changes it later, not a deploy.
"""
from django.db import migrations, models

FREE_CARD_INSIGHTS_PER_MONTH = 5


def set_allowance(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(slug="free").update(
        max_card_insights_per_month=FREE_CARD_INSIGHTS_PER_MONTH)
    Plan.objects.filter(slug="guest").update(max_card_insights_per_month=0)


def clear_allowance(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.update(max_card_insights_per_month=None)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0011_plan_looks'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='max_card_insights_per_month',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='usagerecord',
            name='metric',
            field=models.CharField(choices=[('runs_started', 'Runs started'), ('games_simulated', 'Games simulated'), ('imports', 'Imports'), ('decks_created', 'Decks created'), ('summaries_written', 'Summaries written'), ('card_insights', 'Card insights written')], max_length=32),
        ),        migrations.RunPython(set_allowance, clear_allowance),
    ]
