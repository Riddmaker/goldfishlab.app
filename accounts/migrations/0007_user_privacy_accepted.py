"""`User.privacy_accepted` (D2): new accounts get the current version; the
accounts that exist now have seen only the older one, so theirs is cleared
and they are asked on their next page (`accounts.consent`)."""

from django.db import migrations, models

import accounts.consent


def clear(apps, schema_editor):
    apps.get_model("accounts", "User").objects.update(privacy_accepted=None)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_user_is_system"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="privacy_accepted",
            field=models.DateField(blank=True, default=accounts.consent.current, null=True),
        ),
        migrations.RunPython(clear, migrations.RunPython.noop),
    ]
