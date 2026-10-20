"""Card model phase 4, PR 16b: the patch document is the only store of a correction.

Forward, for every row:

* a current document written for these overrides (``of`` matches) keeps its
  patches and face, and ``said`` takes each primitive's value instead of its
  digest (`simulations.patches`, version 2);
* any other row with overrides gets a document written again from them,
  against the card's reading;
* a row without overrides keeps an empty document.

Every row is then checked: the document states exactly the overrides
(`patches.overrides_of`), or the migration stops before the column goes.

Backward, the overrides are read back out of the documents and each document
is written in version 1 again, with ``of`` and the digests, so the code from
before PR 16b finds what it wrote.
"""

from django.db import migrations

from simulations import patches, primitives

BATCH = 1000


def to_patches_only(apps, schema_editor):
    annotations = apps.get_model("simulations", "CardAnnotation")
    changed = []
    rows = annotations.objects.select_related("oracle_card__profile").order_by("pk")
    for row in rows.iterator(chunk_size=BATCH):
        overrides = row.overrides or {}
        old = row.patches
        if not overrides:
            document = {}
        elif isinstance(old, dict) and old.get("v") == 1 \
                and old.get("of") == patches.digest(overrides):
            document = {"v": patches.VERSION,
                        "said": {key: overrides[key]
                                 for key in sorted(primitives.KEYS & set(overrides))},
                        "face": old["face"], "blocks": old["blocks"]}
        else:
            profile = getattr(row.oracle_card, "profile", None)
            document = patches.from_overrides(overrides, profile)
        if patches.overrides_of(document) != overrides:
            raise RuntimeError(f"annotation {row.pk}: the patches would not state "
                               f"its overrides")
        patches.validate(document)
        row.patches = document
        changed.append(row)
        if len(changed) == BATCH:
            annotations.objects.bulk_update(changed, ["patches"])
            changed = []
    annotations.objects.bulk_update(changed, ["patches"])


def to_overrides(apps, schema_editor):
    annotations = apps.get_model("simulations", "CardAnnotation")
    changed = []
    for row in annotations.objects.order_by("pk").iterator(chunk_size=BATCH):
        document = row.patches
        overrides = patches.overrides_of(document)
        row.overrides = overrides
        if overrides:
            row.patches = {"v": 1, "of": patches.digest(overrides),
                           "said": {key: patches.digest(value)
                                    for key, value in document["said"].items()},
                           "face": document["face"], "blocks": document["blocks"]}
        else:
            row.patches = {}
        changed.append(row)
        if len(changed) == BATCH:
            annotations.objects.bulk_update(changed, ["overrides", "patches"])
            changed = []
    annotations.objects.bulk_update(changed, ["overrides", "patches"])


class Migration(migrations.Migration):

    dependencies = [
        ("simulations", "0017_tags_as_categories"),
        ("cards", "0032_profile_blocks"),
    ]

    operations = [
        migrations.RunPython(to_patches_only, to_overrides),
        migrations.RemoveField(model_name="cardannotation", name="overrides"),
    ]
