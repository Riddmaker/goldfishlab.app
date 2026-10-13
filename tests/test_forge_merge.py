"""Card model phase 1: Forge's blocks fill in what our reader left open.

The scripts are made up for these tests, in the format's shape; none is
copied from Forge's card data.
"""

import pytest

from cards import forge, forge_import, forge_merge, profiles, refresh
from cards.models import DerivedProfile, ForgeScript, OracleCard
from simulation import blocks as b


def blocks_of(text: str) -> tuple[b.Ability, ...]:
    return forge_import.translate(forge.parse(text))


# --- what may be taken ------------------------------------------------------------


def test_only_an_ability_read_whole_is_complete():
    found = blocks_of("""Name:Test Whole
Types:Artifact
A:AB$ Mana | Cost$ T | Produced$ C
A:AB$ Mana | Cost$ T | Produced$ G | IsPresent$ Forest.YouCtrl
A:AB$ Draw | Cost$ T | NumCards$ 1 | UnlessCost$ 2
A:AB$ Draw | Cost$ T Mill<1> | NumCards$ 1
""")
    assert [forge_merge.complete(ability) for ability in found] == [True, False, False, False]


def test_a_value_taken_is_marked_and_answers_its_reason():
    profile = DerivedProfile(review_reasons=["makes mana, but how much could not be read",
                                             "something else"],
                             needs_review=True, source_map={"mv": "scryfall"})
    forge_merge.take(profile, "mana_amount", 2,
                     answers=("makes mana, but how much could not be read",))
    assert profile.mana_amount == 2
    assert profile.source_map == {"mv": "scryfall", "mana_amount": "forge"}
    assert profile.review_reasons == ["something else"]
    assert profile.needs_review
    forge_merge.take(profile, "treasures", 1, answers=("something else",))
    assert not profile.needs_review


# --- where the blocks come from ---------------------------------------------------


def test_derive_merges_the_blocks_of_the_card(catalogue, monkeypatch):
    forge_import.run()
    seen = []
    monkeypatch.setattr(forge_merge, "FAMILIES",
                        [lambda profile, abilities, card: seen.append((card.name, abilities))])
    sol_ring = OracleCard.objects.get(name="Sol Ring")
    profiles.derive(sol_ring)
    assert seen[0][0] == "Sol Ring" and seen[0][1][0].kind == b.MANA
    seen.clear()
    profiles.derive(sol_ring, forge=())
    assert seen == []


def test_blocks_of_an_older_format_are_not_used(catalogue):
    forge_import.run()
    ForgeScript.objects.update(blocks_version=b.VERSION - 1)
    sol_ring = OracleCard.objects.get(name="Sol Ring")
    assert forge_merge.blocks_of(sol_ring) == ()
    assert forge_merge.blocks_by_card([sol_ring]) == {str(sol_ring.pk): ()}


def test_a_card_without_a_script_has_no_blocks(catalogue):
    card = OracleCard.objects.exclude(name__in=["Sol Ring", "Llanowar Elves"]).first()
    assert forge_merge.blocks_of(card) == ()


def test_the_rebuild_hands_each_card_its_own_blocks(catalogue, monkeypatch):
    forge_import.run()
    seen = {}
    monkeypatch.setattr(forge_merge, "FAMILIES", [
        lambda profile, abilities, card: seen.__setitem__(card.name, len(abilities))])
    profiles.rebuild()
    assert seen["Sol Ring"] == seen["Llanowar Elves"] == 1
    assert "Not In The Catalogue" not in seen


def test_new_blocks_derive_the_profiles_again(catalogue):
    profiles.rebuild()
    loaded = refresh.load([], source=None)
    assert loaded.profiles_reason == "Forge's blocks changed"
    assert not refresh.load([], source=None).profiles_reason


# --- the families -----------------------------------------------------------------


def unread_mana(**fields) -> DerivedProfile:
    return DerivedProfile(kind="creature", produces_mana=True, mana_amount=None,
                          review_reasons=["produces mana, but no readable 'Add' clause"],
                          needs_review=True, source_map={}, **fields)


def test_a_mana_ability_the_reader_missed_is_taken():
    profile = unread_mana()
    forge_merge.mana(profile, blocks_of("""Name:Test Yurlok
Types:Creature
A:AB$ Mana | Cost$ 1 T | Produced$ B R G
"""), None)
    assert (profile.mana_amount, profile.mana_produces, profile.mana_activation) == (
        3, {"B": 1, "R": 1, "G": 1}, 1)
    assert profile.source_map["mana_amount"] == "forge"
    assert not profile.needs_review


def test_a_choice_of_colours_is_taken_as_scryfalls_colours():
    profile = unread_mana(mana_colors=["G", "W"])
    forge_merge.mana(profile, blocks_of("""Name:Test Druid
Types:Creature
A:AB$ Mana | Cost$ T | Produced$ Combo G W
"""), None)
    assert (profile.mana_amount, profile.mana_produces, profile.mana_colors) == (
        1, None, ["G", "W"])


@pytest.mark.parametrize("ability", [
    "A:AB$ Mana | Cost$ T | Produced$ C | Amount$ 2 | OpponentTurn$ True",
    "A:AB$ Mana | Cost$ T | Produced$ Any | RestrictValid$ Spell.Creature",
    "A:AB$ Mana | Cost$ U T | Produced$ C | Amount$ 3",
    "A:AB$ Mana | Cost$ 1 T | Produced$ Any",
    "A:AB$ Mana | Cost$ T | Produced$ G | Amount$ X\nSVar:X:Count$Valid Elf.YouCtrl",
])
def test_a_mana_ability_is_not_taken_when_the_engine_would_play_it_otherwise(ability):
    profile = unread_mana()
    forge_merge.mana(profile, blocks_of(f"Name:Test\nTypes:Creature\n{ability}\n"), None)
    assert profile.mana_amount is None and profile.needs_review


def test_a_mana_reading_is_never_overridden():
    profile = DerivedProfile(kind="land", produces_mana=True, mana_amount=1,
                             mana_produces={"U": 1}, source_map={})
    forge_merge.mana(profile, blocks_of("""Name:Test Gate
Types:Land
A:AB$ Mana | Cost$ T | Produced$ C | Amount$ 2
"""), None)
    assert (profile.mana_amount, profile.mana_produces) == (1, {"U": 1})
