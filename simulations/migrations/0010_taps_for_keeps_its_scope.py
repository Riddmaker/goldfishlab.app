"""The editor rebuild: "Taps for" now replaces only a card's own tap ability.

Before it, an annotation that said what a card taps for switched every mana
reading of the card off - an altar, tapping others, a Mox, counters, a
trigger's mana. Each of those is now its own override key. This migration
writes the old meaning into every stored annotation that says "Taps for", so
no judgement anyone made moves: the readings it silenced get an explicit "off".

The rule is copied from `simulations.primitives.keep_taps_for_scope` rather
than imported, so a later change there cannot rewrite history here.
"""

from django.db import migrations

_SAYS = ("mana_produces", "mana_black", "mana_colorless")


def _kept(overrides: dict, profile) -> dict:
    kept = dict(overrides)
    for key in ("sacrifice_mana", "tap_mana", "mana_store"):
        if getattr(profile, key, None) and key not in kept:
            kept[key] = None
    if getattr(profile, "mox", "") and "mox" not in kept:
        kept["mox"] = ""
    triggers = getattr(profile, "triggers", None) or []
    if any(read.get("mana") for read in triggers) and "triggers" not in kept:
        kept["triggers"] = [{name: item for name, item in read.items()
                             if name not in ("mana", "color")} for read in triggers]
    return kept


def forwards(apps, schema_editor):
    annotation_model = apps.get_model("simulations", "CardAnnotation")
    profile_model = apps.get_model("cards", "DerivedProfile")
    for annotation in annotation_model.objects.all().iterator():
        overrides = annotation.overrides or {}
        if not any(key in overrides for key in _SAYS):
            continue
        profile = profile_model.objects.filter(oracle_card_id=annotation.oracle_card_id).first()
        if profile is None:
            continue
        kept = _kept(overrides, profile)
        if kept != overrides:
            # `update`, not `save`: the historical model has no `clean`.
            annotation_model.objects.filter(pk=annotation.pk).update(overrides=kept)


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0029_combat"),
        ("simulations", "0009_unread_cards"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
