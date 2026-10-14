"""Card model phase 2: each stored patch document says its primitives' digests.

`said` is the digest of each primitive's value as it was said. With it, a
correction whose block is gone survives a save of another block of the card
until its owner keeps or discards it (`simulations.patches.stated`).

A document written for the overrides beside it (`of` matches) says exactly
those values, so the digests are taken from the overrides: nothing is
compiled, and no correction or engine card changes. A document that does not
match is left alone - it is not played, and the next save rewrites it.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    from simulations import patches, primitives

    annotation_model = apps.get_model("simulations", "CardAnnotation")
    for annotation in annotation_model.objects.exclude(overrides={}).iterator():
        document = annotation.patches
        if not patches.is_current(document) \
                or document.get("of") != patches.digest(annotation.overrides):
            continue
        document["said"] = {key: patches.digest(value)
                            for key, value in annotation.overrides.items()
                            if key in primitives.KEYS}
        # `update`, not `save`: the historical model has no `clean`.
        annotation_model.objects.filter(pk=annotation.pk).update(patches=document)


class Migration(migrations.Migration):

    dependencies = [
        ("simulations", "0014_merge_patches_list_print"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
