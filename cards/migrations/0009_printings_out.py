"""The printing catalogue goes, and so do the collection app's leftovers (phase 9 I).

`Printing` was only ever read for the collection's prices and for two import
rungs that production never had data for: `default_cards` was opt-in and was
never loaded there, so the table is empty. The `BulkImport` rows that recorded
loading it (development machines only) go with it.

The `collection` app itself left `INSTALLED_APPS` in the same release; its tables
were dropped by `collection/0004` one deploy earlier, as Django's "How to delete
a Django application" prescribes. What neither step removes is the content types
- Django never deletes those on its own - so this migration does what
`remove_stale_contenttypes --include-stale-apps` would, and production needs no
manual step. Deleting a content type cascades to its permissions.
"""

from django.db import migrations, models

#: (app_label, model) pairs whose content types are stale after this release;
#: `None` as the model means every model of that app.
STALE = [("collection", None), ("cards", "printing")]


def drop_stale_content_types(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    for app_label, model in STALE:
        stale = ContentType.objects.filter(app_label=app_label)
        if model:
            stale = stale.filter(model=model)
        stale.delete()


def drop_printing_imports(apps, schema_editor):
    BulkImport = apps.get_model("cards", "BulkImport")
    BulkImport.objects.filter(kind="default_cards").delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cards', '0008_derivedprofile_mana_costs'),
        # The cascade from a content type reaches permissions and admin log
        # entries, so both must be in the migration state it runs in.
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]

    operations = [
        migrations.RunPython(drop_printing_imports, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='bulkimport',
            name='kind',
            field=models.CharField(choices=[('oracle_cards', 'Oracle cards'), ('oracle_tags', 'Oracle tags')], max_length=32),
        ),
        migrations.DeleteModel(
            name='Printing',
        ),
        migrations.RunPython(drop_stale_content_types, migrations.RunPython.noop),
    ]
