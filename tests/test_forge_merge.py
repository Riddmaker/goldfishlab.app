"""Card model phase 1: Forge's blocks fill in what our reader left open.

The scripts are made up for these tests, in the format's shape; none is
copied from Forge's card data.
"""

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
