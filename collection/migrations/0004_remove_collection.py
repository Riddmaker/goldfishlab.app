"""Drop the collection tables (phase 9 B). Production never held a row."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('collection', '0003_column_mapping'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='collectionitem',
            name='collection',
        ),
        migrations.RemoveField(
            model_name='collectionitem',
            name='oracle_card',
        ),
        migrations.RemoveField(
            model_name='collectionitem',
            name='printing',
        ),
        migrations.DeleteModel(
            name='Collection',
        ),
        migrations.DeleteModel(
            name='CollectionItem',
        ),
    ]
