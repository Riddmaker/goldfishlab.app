from django.db import migrations, models


class Migration(migrations.Migration):
    """Strict or free (Paket 4, PR 18). Every session from before is strict."""

    dependencies = [
        ("playtest", "0003_playtestsession_engine_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="playtestsession",
            name="mode",
            field=models.CharField(choices=[("strict", "Strict"), ("free", "Free")],
                                   default="strict", max_length=8),
        ),
    ]
