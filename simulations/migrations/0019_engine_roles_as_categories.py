"""Card model phase 4, PR 17b: the engine's roles are read off the categories.

Every correction that still says the engine's roles its own way - the
"Roles the engine reads" judgement (``engine_tags``) or a whole list from
before phase 3 (``tags``) - is said in the card's categories instead
(`adapter.roles_as_categories`), so the engine plays the card in the same
roles:

* a player's list first becomes the categories it counted the card in, as
  migration 0017 would have said it (`annotations.tags_as_categories`);
* the roles become ``category_add`` / ``category_remove``, relative to the
  card's categories as derived again (below);
* a row with nothing left to say goes.

The categories are those the card data gives once the profile is derived
again (`cards.categories.derive`, version 2: which abilities draw), since
that is what the corrections will be relative to and the roles read off;
the stored profile is not changed. The keys are face values of the patch
document, never primitives, so only ``face`` changes. A role the
categories cannot give is printed (a draw engine on a card none of whose
drawing abilities repeats); the engine then plays the card by its
categories.

Backward does nothing: the categories it wrote keep counting, and the code
from before PR 17 reads its roles from the community tags again.
"""

import copy

from django.db import migrations

from cards import categories, forge_merge
from cards.models import OracleCard
from simulations import annotations, patches
from simulations.engine import adapter, runner

BATCH = 1000
ENGINE = frozenset(runner.ENGINE_ROLE_TAGS)


def wanted_roles(face: dict) -> frozenset:
    """The roles a row said the engine plays the card in."""
    if "engine_tags" in face:
        return frozenset(face["engine_tags"] or ())
    return frozenset(face["tags"] or ()) & ENGINE


def said_in_categories(face: dict, profile, builtin: bool) -> tuple[dict, frozenset]:
    """The row's face with its roles said in its categories."""
    wanted = wanted_roles(face)
    if "tags" in face and not builtin:
        # A player's whole list counted in the statistics; a built-in one never did.
        converted = annotations.tags_as_categories(
            face, adapter.derived_categories(profile), profile.role_tags, explicit=True)
        face = {key: value for key, value in converted.items() if key != "engine_tags"}
    face = {key: value for key, value in face.items() if key not in ("tags", "engine_tags")}
    return adapter.roles_as_categories(face, profile, wanted)


def derived_again(oracle_id):
    """The card's profile as the next derivation will write its categories;
    the live models, since the engine reads them the way it does today."""
    card = OracleCard.objects.select_related("profile", "forge").get(pk=oracle_id)
    future = copy.copy(card.profile)
    found = categories.derive(future.kind, forge_merge.blocks_of(card),
                              future.role_tags, future)
    future.primary_role, future.secondary_role = found.primary, found.secondary
    future.mechanics = list(found.mechanics)
    future.tempo = found.tempo
    future.categories_version = categories.VERSION
    return future


def forwards(apps, schema_editor):
    annotations_model = apps.get_model("simulations", "CardAnnotation")
    rows = (annotations_model.objects
            .filter(patches__face__has_any_keys=["tags", "engine_tags"])
            .select_related("oracle_card").order_by("pk"))
    changed, gone, missed = [], [], []
    for row in rows.iterator(chunk_size=BATCH):
        document = dict(row.patches)
        face, lost = said_in_categories(document["face"], derived_again(row.oracle_card_id),
                                        builtin=row.owner_id is None)
        document["face"] = face
        patches.validate(document)
        if lost:
            missed.append(f"{row.oracle_card.name}: {', '.join(sorted(lost))}")
        if not patches.overrides_of(document) and not row.note and not row.confirmed:
            gone.append(row.pk)
            continue
        row.patches = document
        changed.append(row)
    annotations_model.objects.bulk_update(changed, ["patches"], batch_size=BATCH)
    annotations_model.objects.filter(pk__in=gone).delete()
    if missed:
        print(f"\n  the categories cannot give {len(missed)} role(s): " + "; ".join(missed))


class Migration(migrations.Migration):

    dependencies = [
        ("simulations", "0018_patches_only"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
