"""Card model phase 2, PR 7: a player's `tutor_count` becomes the Search block.

The judgement form no longer asks how many cards a tutor finds - the Search
block says it, beside the zone and what it may find. A player's row that
says it the old way is said the new way (`annotations.tutor_as_block`), but
only where the engine plays the card exactly as before: built with what
lies under the row in a deck (the built-in correction, and for a deck's row
the owner's own for all decks) both ways, field by field. A row where it
would not is left as it is; the adapter still reads `tutor_count`.

Built-in rows are not touched: the reference deck's corrections are seeded
in the old keys, and nobody edits them on a card page. The patches are
written again for each converted row, as on any save.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    annotation_model = apps.get_model("simulations", "CardAnnotation")
    rows = annotation_model.objects.filter(owner__isnull=False,
                                           overrides__has_key="tutor_count")
    if not rows.exists():
        return
    # The live code, not the historical models: the engine has to build the
    # card the way it builds it today.
    from cards.models import OracleCard
    from simulations import annotations, patches
    from simulations.engine import adapter

    for row in rows.iterator():
        said = annotations.tutor_as_block(row.overrides)
        card = OracleCard.objects.select_related("profile").filter(
            pk=row.oracle_card_id).first()
        if said is None or card is None:
            continue
        under = _under(annotation_model, row)
        before, _gaps = adapter.card_with(card, under | row.overrides)
        after, _gaps = adapter.card_with(card, under | said)
        if before != after:
            continue
        profile = getattr(card, "profile", None)
        # `update`, not `save`: the historical model has no `clean`.
        annotation_model.objects.filter(pk=row.pk).update(
            overrides=said,
            patches=patches.from_overrides(said, profile, previous=row.patches))


def _under(annotation_model, row) -> dict:
    """What a deck merges under this row, key by key: the built-in correction,
    and for a deck's row its owner's for all decks."""
    merged = {}
    lower = [annotation_model.objects.filter(owner__isnull=True, deck__isnull=True,
                                             oracle_card_id=row.oracle_card_id).first()]
    if row.deck_id is not None:
        lower.append(annotation_model.objects.filter(
            owner_id=row.owner_id, deck__isnull=True,
            oracle_card_id=row.oracle_card_id).first())
    for found in lower:
        if found is not None:
            merged |= found.overrides or {}
    return merged


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0030_forge_scripts"),
        ("simulations", "0015_patches_said"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
