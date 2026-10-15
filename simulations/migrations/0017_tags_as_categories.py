"""Card model phase 3, PR 10: a player's whole list of roles is said apart.

Before phase 3 one list (``tags``) said both what the engine plays a card as
and what it counts as in the statistics. Now the four roles the engine reads
are ``engine_tags``, and what the card counts as is ``category_add`` and
``category_remove`` relative to its derived categories
(`annotations.tags_as_categories`). The categories are those the card data
gives once the profile is derived again (`cards.categories.derive`), since
that is what the corrections will be relative to.

A list is converted only where the engine plays the card exactly as before
and the statistics count it as before, in every deck the rows reach - the
roles the engine does not read and the keys outside the vocabulary aside,
which nothing plays or counts. Each key is read with the scope that set it,
as in a deck: a built-in roles list under the row counts for the engine
only, so where one is there the four roles are stated outright. An owner's
rows for one card are converted all together or not at all, since a deck's
row merges over the one for all decks key by key. A row left as it is is
still read the old way, and the card page offers to retire it.

Built-in rows are not touched: the reference deck's corrections are a
statement about how the engine plays the card (`adapter._categories`). The
patches are written again for each converted row, as on any save.
"""

import copy
import dataclasses

from django.db import migrations


def forwards(apps, schema_editor):
    annotation_model = apps.get_model("simulations", "CardAnnotation")
    rows = annotation_model.objects.filter(owner__isnull=False, overrides__has_key="tags")
    if not rows.exists():
        return
    # The live code, not the historical models: the engine has to build the
    # card the way it builds it today.
    from cards import categories, forge_merge
    from cards.models import OracleCard
    from simulations import annotations, patches
    from simulations.engine import adapter

    groups = set(rows.values_list("owner_id", "oracle_card_id"))
    for owner_id, oracle_id in sorted(groups, key=str):
        card = OracleCard.objects.select_related("profile").filter(pk=oracle_id).first()
        profile = getattr(card, "profile", None) if card is not None else None
        if profile is None:
            continue
        future = _derived_again(card, profile, categories, forge_merge)
        derived = adapter.derived_categories(future.profile)
        builtin = annotation_model.objects.filter(owner__isnull=True, deck__isnull=True,
                                                  oracle_card_id=oracle_id).first()
        mine = list(annotation_model.objects.filter(owner_id=owner_id,
                                                    oracle_card_id=oracle_id))
        before = {row.pk: row.overrides or {} for row in mine}
        # The fewest keys first; the four roles stated outright if that is
        # what it takes to play the card as before.
        for explicit in (False, True):
            said = {row.pk: annotations.tags_as_categories(before[row.pk], derived,
                                                           profile.role_tags, explicit)
                    for row in mine}
            after = {pk: said[pk] if said[pk] is not None else before[pk] for pk in before}
            if all(_plays(adapter, future, *_merged(adapter, builtin, mine, before, deck))
                   == _plays(adapter, future, *_merged(adapter, builtin, mine, after, deck))
                   for deck in _decks(mine)):
                break
        else:
            continue
        for row in mine:
            if said[row.pk] is None:
                continue
            # `update`, not `save`: the historical model has no `clean`.
            annotation_model.objects.filter(pk=row.pk).update(
                overrides=said[row.pk],
                patches=patches.from_overrides(said[row.pk], profile,
                                               previous=row.patches))


def _derived_again(card, profile, categories, forge_merge):
    """The card with its profile as the next derivation will write it; the
    stored profile is not changed."""
    future = copy.copy(profile)
    found = categories.derive(profile.kind, forge_merge.blocks_of(card),
                              profile.role_tags, profile)
    future.primary_role, future.secondary_role = found.primary, found.secondary
    future.mechanics = list(found.mechanics)
    future.categories_version = categories.VERSION
    # A model's copy has a cache of its own (`Model.__getstate__`): the
    # engine reads the future profile, the stored card keeps its own.
    shown = copy.copy(card)
    shown._state.fields_cache["profile"] = future
    return shown


def _decks(mine) -> list:
    """Every deck the rows reach: each of the owner's decks with a row for
    the card, and one without (`None`)."""
    return [None, *sorted({row.deck_id for row in mine if row.deck_id is not None})]


def _merged(adapter, builtin, mine, overrides: dict, deck_id) -> tuple[dict, dict]:
    """What a deck merges for the card, key by key, and which scope set each
    key: built in, then the owner's for all decks, then the deck's own."""
    layers = [(adapter.BUILTIN_SCOPE, builtin.overrides or {})] if builtin is not None else []
    layers += [(adapter.USER_SCOPE, overrides[row.pk]) for row in mine if row.deck_id is None]
    layers += [(adapter.DECK_SCOPE, overrides[row.pk]) for row in mine
               if row.deck_id is not None and row.deck_id == deck_id]
    merged, scopes = {}, {}
    for scope, said in layers:
        merged |= said
        scopes |= dict.fromkeys(said, scope)
    return merged, scopes


def _plays(adapter, card, overrides: dict, scopes: dict):
    """The engine card, with what nothing plays or counts left out: the
    roles the engine does not read, and categories outside the vocabulary."""
    from cards import categories
    from simulations.engine import runner

    found, _gaps = adapter.card_with(card, overrides, scopes)
    known = set(categories.ROLES + categories.MECHANICS)
    return dataclasses.replace(found, tags=found.tags & set(runner.ENGINE_ROLE_TAGS),
                               categories=found.categories & known)


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0031_profile_categories"),
        ("simulations", "0016_tutor_count_as_block"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
