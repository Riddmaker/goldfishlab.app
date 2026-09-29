"""A pending import is a deck upload only, since the collection went (phase 9 B).

An abandoned collection upload waiting on the mapping screen would otherwise sit
in the table with a kind nothing reads any more, until housekeeping deleted it a
week later - and the mapping view would import it as a *deck* if somebody
answered it. So those rows go now. Not reversible: the collection app they were
for no longer exists.
"""

from django.db import migrations, models


def drop_collection_uploads(apps, schema_editor):
    PendingImport = apps.get_model("decks", "PendingImport")
    PendingImport.objects.filter(kind="collection").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("decks", "0004_commander_out_of_the_99"),
    ]

    operations = [
        migrations.RunPython(drop_collection_uploads, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pendingimport",
            name="kind",
            field=models.CharField(choices=[("deck", "Deck")], max_length=16),
        ),
    ]
