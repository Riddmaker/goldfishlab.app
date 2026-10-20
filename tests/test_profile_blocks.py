"""Card model phase 4, PR 16: a profile's mechanisms as one field.

What these tests hold:

1. **The profile's columns are what is not a mechanism**: identity, cost,
   tags, categories, review, and `blocks`; every mechanism of
   `cards.mechanisms.COLUMNS` is a property over `blocks` (PR 16b).
2. **The field round-trips**: `unpack(pack(...))` gives every mechanism
   back, defaults are left out, the tutor's four sit under ``tutor``.
3. **A property reads and writes the field**: the default where it says
   nothing, the default written takes the key out, a save keeps it.
4. **Every write keeps it**: a derived catalogue carries blocks equal to
   what its mechanisms read.
"""

import pytest

from cards import mechanisms
from cards.models import DerivedProfile, OracleCard

#: The profile's columns: everything that is not a mechanism.
COLUMNS = {"oracle_card", "mv", "pips", "generic", "colorless", "has_x", "kind",
           "is_basic_swamp", "role_tags", "produces_mana", "mana_colors", "mana_amount",
           "primary_role", "secondary_role", "mechanics", "tempo", "category_review",
           "categories_version", "blocks", "needs_review", "review_reasons", "source_map",
           "derived_at"}

#: A value away from the default for every mechanism.
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


def test_the_columns_are_what_is_not_a_mechanism():
    names = {field.name for field in DerivedProfile._meta.concrete_fields}
    assert names == COLUMNS
    assert not set(mechanisms.COLUMNS) & names
    assert all(isinstance(getattr(DerivedProfile, column), property)
               for column in mechanisms.COLUMNS)


def test_every_mechanism_comes_back():
    assert set(EVERY) == set(mechanisms.COLUMNS)
    profile = DerivedProfile(**EVERY)
    assert {column: getattr(profile, column) for column in mechanisms.COLUMNS} == EVERY
    assert mechanisms.pack(profile) == profile.blocks
    assert mechanisms.unpack(profile.blocks) == EVERY
    assert profile.blocks["tutor"] == {name: EVERY[name] for name in mechanisms.GROUPS["tutor"]}
    assert "tutor_to" not in profile.blocks


def test_defaults_are_left_out():
    profile = DerivedProfile()
    assert profile.blocks == {} and mechanisms.pack(profile) == {}
    assert {column: getattr(profile, column) for column in mechanisms.COLUMNS} == {
        column: mechanisms.default(column) for column in mechanisms.COLUMNS}
    assert profile.triggers is not DerivedProfile().triggers


def test_writing_the_default_takes_the_key_out():
    profile = DerivedProfile(draws_cards=2, tutor_to="hand", tutor_count=1)
    profile.draws_cards = None
    profile.tutor_to, profile.tutor_count = "", None
    assert profile.blocks == {}


@pytest.mark.django_db
def test_a_derived_catalogue_carries_its_blocks(catalogue):
    profiles = list(DerivedProfile.objects.all())
    assert profiles and any(profile.blocks for profile in profiles)
    for profile in profiles:
        assert profile.blocks == mechanisms.pack(profile), profile


@pytest.mark.django_db
def test_a_saved_mechanism_is_kept_in_its_field(catalogue):
    profile = OracleCard.objects.filter(profile__isnull=False).first().profile
    profile.draws_cards = 3
    profile.save(update_fields=["draws_cards"])
    profile = DerivedProfile.objects.get(pk=profile.pk)
    assert profile.draws_cards == 3 and profile.blocks["draws_cards"] == 3
