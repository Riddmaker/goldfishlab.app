"""Card model phase 2: every stored correction gets its patches.

`CardAnnotation.patches` is the same correction as patches on the blocks of
the card's reading (`simulations.patches`). This migration writes them for
every row that has overrides; the overrides themselves are not touched, so
nothing a person decided is rewritten, and a rollback reads them as before.

Unlike 0010, the conversion is imported rather than copied: it changes no
stored value, and `from_overrides` checks each primitive it writes - compiled
against the same reading it gives the override back exactly, or it is stored
whole - so the engine plays every row as it did.
"""

from django.db import migrations, models


def forwards(apps, schema_editor):
    from simulations import patches

    annotation_model = apps.get_model("simulations", "CardAnnotation")
    profile_model = apps.get_model("cards", "DerivedProfile")
    for annotation in annotation_model.objects.exclude(overrides={}).iterator():
        profile = profile_model.objects.filter(oracle_card_id=annotation.oracle_card_id).first()
        document = patches.from_overrides(annotation.overrides, profile)
        # `update`, not `save`: the historical model has no `clean`.
        annotation_model.objects.filter(pk=annotation.pk).update(patches=document)


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0030_forge_scripts"),
        ("simulations", "0010_taps_for_keeps_its_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="cardannotation",
            name="patches",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
