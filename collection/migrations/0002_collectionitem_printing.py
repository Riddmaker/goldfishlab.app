"""Give every collection item the printing it always described.

The backfill is the reason `CollectionItem` kept the raw `scryfall_id` string
instead of discarding it once the card had resolved. It is the whole payoff of
that decision: nobody has to re-upload their collection to get prices.

It matches on the printing id alone and nothing else. That is exact and has no
interpretation in it - no set-code casing, no collector-number punctuation, no
language. A row that does not match is left null, which is the same "nobody
knows" every other null here means, and a re-import resolves it through the
full ladder.
"""

import django.db.models.deletion
from django.db import migrations, models

BATCH = 2_000


def link_printings(apps, schema_editor):
    CollectionItem = apps.get_model("collection", "CollectionItem")
    Printing = apps.get_model("cards", "Printing")

    known = set(Printing.objects.values_list("scryfall_id", flat=True))
    if not known:
        return  # no printings ingested: nothing to link, and not a problem

    known = {str(pk) for pk in known}
    pending = []
    items = CollectionItem.objects.filter(printing__isnull=True).exclude(scryfall_id="")
    for item in items.only("pk", "scryfall_id").iterator(chunk_size=BATCH):
        if item.scryfall_id in known:
            item.printing_id = item.scryfall_id
            pending.append(item)
        if len(pending) >= BATCH:
            CollectionItem.objects.bulk_update(pending, ["printing"])
            pending = []

    if pending:
        CollectionItem.objects.bulk_update(pending, ["printing"])


def unlink_printings(apps, schema_editor):
    """Reversing drops the link and keeps every string it was derived from."""
    apps.get_model("collection", "CollectionItem").objects.update(printing=None)


class Migration(migrations.Migration):

    dependencies = [
        ('cards', '0007_alter_bulkimport_kind_printing'),
        ('collection', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='collectionitem',
            name='printing',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='collection_items', to='cards.printing'),
        ),
        migrations.RunPython(link_printings, unlink_printings),
    ]
