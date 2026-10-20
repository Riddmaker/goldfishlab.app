"""Card model phase 4, PR 16a: a profile's mechanisms as one field.

What these tests hold:

1. **The mechanism columns are named once**: `cards.mechanisms.COLUMNS`
   holds every column of the profile that is not identity, cost, tags,
   categories or review, each with the model's default.
2. **The field round-trips**: `unpack(pack(...))` gives every column back,
   defaults are left out, the tutor's four columns sit under ``tutor``.
3. **Every write keeps it**: a derived catalogue and a saved profile carry
   ``blocks`` equal to their columns.
"""

import pytest

from cards import mechanisms
from cards.models import DerivedProfile, OracleCard

#: The profile's columns that are not mechanisms.
OTHER = {"oracle_card", "mv", "pips", "generic", "colorless", "has_x", "kind",
         "is_basic_swamp", "role_tags", "produces_mana", "mana_colors", "mana_amount",
         "primary_role", "secondary_role", "mechanics", "tempo", "category_review",
         "categories_version", "blocks", "needs_review", "review_reasons", "source_map",
         "derived_at"}

#: A value away from the default for every column.
EVERY = {
    "enters_tapped": True, "mana_produces": {"B": 1}, "mana_activation": 1,
    "mana_untaps": False, "mana_condition": {"if": {"kind": "lands", "count": 5}},
    "mana_rule": {"rule": "type_adding", "subtype": "swamp"}, "mana_filter": {"amount": 2},
    "mana_spend_only": {"spells": [{"types": ["creature"]}]}, "mana_chosen_color": True,
    "treasures": 1, "landers": 1, "discard_cost": 1, "additional_cost": [{"life": 2}],
    "sacrifice_mana": {"sacrifice": ["creature"], "amount": 2}, "mana_from_hand": True,
    "tap_mana": {"types": ["artifact"]}, "mox": "imprint", "mana_mills": 1,
    "mana_store": {"use": "free"}, "growth": {"enters": 2}, "combat": {"germ": True},
    "triggers": [{"event": "landfall", "treasures": 1}], "cost_reduction": 1,
    "draws_cards": 2, "discards_after": 1, "puts_back": 2, "draws_x": True,
    "extra_cost": "{B}{B}", "tutor_to": "hand", "tutor_count": 1, "tutor_kind": "creature",
    "tutor_filter": {"types": ["creature"]}, "land_search": {"battlefield": 1},
    "tapped_unless": {"kind": "pay_life", "count": 2}, "skips_draw_step": True,
    "self_life_loss": 2, "opponent_life_loss": 1,
}


def test_every_column_is_a_mechanism_or_named_as_something_else():
    names = {field.name for field in DerivedProfile._meta.concrete_fields}
    assert set(mechanisms.COLUMNS) | OTHER == names
    assert not set(mechanisms.COLUMNS) & OTHER


@pytest.mark.parametrize("column", list(mechanisms.COLUMNS))
def test_a_column_has_the_models_default(column):
    assert mechanisms.default(column) == DerivedProfile._meta.get_field(column).get_default()


def test_every_column_comes_back():
    assert set(EVERY) == set(mechanisms.COLUMNS)
    profile = DerivedProfile(**EVERY)
    blocks = mechanisms.pack(profile)
    assert mechanisms.unpack(blocks) == EVERY
    assert blocks["tutor"] == {name: EVERY[name] for name in mechanisms.GROUPS["tutor"]}
    assert "tutor_to" not in blocks


def test_defaults_are_left_out():
    profile = DerivedProfile()
    assert mechanisms.pack(profile) == {}
    assert mechanisms.unpack({}) == {column: mechanisms.default(column)
                                     for column in mechanisms.COLUMNS}
    assert mechanisms.default("triggers") is not mechanisms.default("triggers")


@pytest.mark.django_db
def test_a_derived_catalogue_carries_its_blocks(catalogue):
    profiles = list(DerivedProfile.objects.all())
    assert profiles and any(profile.blocks for profile in profiles)
    for profile in profiles:
        assert profile.blocks == mechanisms.pack(profile), profile
        assert mechanisms.unpack(profile.blocks) == {
            column: getattr(profile, column) for column in mechanisms.COLUMNS}


@pytest.mark.django_db
def test_a_saved_profile_writes_its_blocks(catalogue):
    profile = OracleCard.objects.filter(profile__isnull=False).first().profile
    profile.draws_cards = 3
    profile.save(update_fields=["draws_cards"])
    profile.refresh_from_db()
    assert profile.blocks["draws_cards"] == 3
