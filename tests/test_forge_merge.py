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


def unread_tapped() -> DerivedProfile:
    return DerivedProfile(
        kind="land", enters_tapped=False, source_map={}, needs_review=True,
        review_reasons=["text mentions entering tapped, but the condition was not readable"])


def tapped_by(replacement: str) -> DerivedProfile:
    profile = unread_tapped()
    forge_merge.enters_tapped(profile, blocks_of(
        f"Name:Test\nTypes:Land\nR:Event$ Moved | ValidCard$ Card.Self | Destination$ "
        f"Battlefield | ReplaceWith$ ETBTapped{replacement}\n"
        "SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True\n"), None)
    return profile


def test_a_card_that_enters_tapped_by_its_short_name_is_taken():
    profile = tapped_by("")
    assert profile.enters_tapped and profile.tapped_unless is None
    assert not profile.needs_review and profile.source_map["enters_tapped"] == "forge"


@pytest.mark.parametrize("unless, condition", [
    ("Reveal<1/Elf>", {"kind": "reveal", "types": ["elf"]}),
    ("PayLife<3>", {"kind": "pay_life", "life": 3}),
])
def test_a_condition_the_engine_checks_is_taken_with_it(unless, condition):
    profile = unread_tapped()
    forge_merge.enters_tapped(profile, blocks_of(f"""Name:Test
Types:Land
R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped
SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True | UnlessCost$ {unless}
"""), None)
    assert profile.enters_tapped and profile.tapped_unless == condition
    assert not profile.needs_review


def test_a_condition_the_engine_cannot_check_is_played_tapped_and_still_said():
    profile = unread_tapped()
    forge_merge.enters_tapped(profile, blocks_of("""Name:Test
Types:Land
R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped
SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True | ConditionPresent$ Land.YouCtrl+Other \
| ConditionCompare$ GE2
"""), None)
    assert profile.enters_tapped and profile.tapped_unless is None
    assert profile.needs_review


def test_a_choice_to_enter_tapped_is_not_taken():
    profile = unread_tapped()
    forge_merge.enters_tapped(profile, blocks_of("""Name:Test
Types:Land
R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped \
| Optional$ True
SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True | SubAbility$ DBRad
SVar:DBRad:DB$ Radiation | Num$ 2
"""), None)
    assert not profile.enters_tapped


def searched(kind: str, script: str) -> DerivedProfile:
    profile = DerivedProfile(kind=kind, land_search=None, tutor_to="battlefield",
                             tutor_count=1, source_map={"tutor_to": "tags"}, needs_review=True,
                             review_reasons=["tutors onto the battlefield, which the engine "
                                             "cannot do"])
    forge_merge.land_search(profile, blocks_of(f"Name:Test\n{script}"), None)
    return profile


def test_a_spells_land_search_is_taken_and_is_no_tutor_any_more():
    profile = searched("sorcery", """Types:Sorcery
A:SP$ ChangeZone | Origin$ Library | Destination$ Battlefield | ChangeType$ Land \
| ChangeNum$ 10 | Tapped$ True | SubAbility$ DBShuffle
SVar:DBShuffle:DB$ Shuffle | Defined$ You
""")
    spec = profile.land_search
    assert (spec["battlefield"], spec["tapped"], spec["basic"], spec["types"], spec["when"]) == (
        10, True, False, [], "cast")
    assert (profile.tutor_to, profile.tutor_count) == ("", None)
    assert profile.source_map == {"land_search": "forge"}
    assert not profile.needs_review


def test_a_search_as_the_card_enters_or_for_its_cost_is_taken():
    entering = searched("creature", """Types:Creature Human
T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self \
| Execute$ TrigSearch
SVar:TrigSearch:DB$ ChangeZone | Origin$ Library | Destination$ Battlefield \
| ChangeType$ Mountain.Basic | Tapped$ True
""").land_search
    assert (entering["when"], entering["basic"], entering["types"]) == (
        "enters", True, ["mountain"])
    activated = searched("land", """Types:Land
A:AB$ ChangeZone | Cost$ 1 T Sac<1/CARDNAME> | Origin$ Library | Destination$ Battlefield \
| ChangeType$ Land.Basic | Tapped$ True
""").land_search
    assert (activated["when"], activated["cost"], activated["taps"], activated["sacrifice"]) == (
        "activate", "{1}", True, True)


@pytest.mark.parametrize("kind, script", [
    # As it dies: no moment the engine plays a search at.
    ("creature", "Types:Creature\nT:Mode$ ChangesZone | Origin$ Battlefield | Destination$ "
     "Graveyard | ValidCard$ Card.Self | Execute$ TrigSearch\nSVar:TrigSearch:DB$ ChangeZone | "
     "Origin$ Library | Destination$ Battlefield | ChangeType$ Land.Basic"),
    # A land the engine cannot describe.
    ("sorcery", "Types:Sorcery\nA:SP$ ChangeZone | Origin$ Library | Destination$ Battlefield "
     "| ChangeType$ Land.Basic,Desert"),
    # For another player.
    ("instant", "Types:Instant\nA:SP$ ChangeZone | Origin$ Library | Destination$ Battlefield "
     "| ChangeType$ Land.Basic | DefinedPlayer$ TargetedController"),
    # Paid for as it resolves.
    ("artifact", "Types:Artifact\nT:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield "
     "| ValidCard$ Card.Self | Execute$ TrigSearch\nSVar:TrigSearch:AB$ ChangeZone | Cost$ "
     "Sac<1/Land> | Origin$ Library | Destination$ Battlefield | ChangeType$ Land.Basic"),
    # One of a charm's modes.
    ("sorcery", "Types:Sorcery\nA:SP$ Charm | Choices$ DBSearch,DBDraw\nSVar:DBSearch:DB$ "
     "ChangeZone | Origin$ Library | Destination$ Battlefield | ChangeType$ Land.Basic\n"
     "SVar:DBDraw:DB$ Draw | NumCards$ 1"),
])
def test_a_land_search_the_engine_would_play_otherwise_is_not_taken(kind, script):
    assert searched(kind, script).land_search is None


ENTERS = ("T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self "
          "| Execute$ TrigToken\n")


def treasure_of(kind: str, script: str, text: str = "") -> DerivedProfile:
    profile = DerivedProfile(kind=kind, treasures=0, source_map={},
                             review_reasons=["only grants a mana ability to another permanent"],
                             needs_review=True)
    forge_merge.treasures(profile, blocks_of(f"Name:Test\n{script}"),
                          OracleCard(name="Test", oracle_text=text))
    return profile


def test_a_treasure_made_as_the_card_enters_is_taken():
    profile = treasure_of("creature", "Types:Creature\n" + ENTERS
                          + "SVar:TrigToken:DB$ Token | TokenScript$ c_a_treasure_sac\n",
                          'When this creature enters, create a Treasure token. (It\'s an '
                          'artifact with "{T}, Sacrifice this token: Add one mana of any '
                          'color.")')
    assert profile.treasures == 1 and profile.source_map == {"treasures": "forge"}
    assert not profile.needs_review


def test_a_spells_treasures_are_taken_beside_its_other_tokens():
    profile = treasure_of("sorcery", "Types:Sorcery\nA:SP$ Token | TokenScript$ u_1_1_fish "
                          "| TokenAmount$ 2 | SubAbility$ DBTreasure\nSVar:DBTreasure:DB$ "
                          "Token | TokenScript$ c_a_treasure_sac | TokenAmount$ 2\n")
    assert profile.treasures == 2


@pytest.mark.parametrize("kind, script", [
    ("instant", "Types:Instant\nA:SP$ Token | TokenScript$ c_a_treasure_sac | TokenAmount$ 2 "
     "| TokenOwner$ TargetedController"),
    ("creature", "Types:Creature\n" + ENTERS + "SVar:TrigToken:DB$ Token | TokenScript$ "
     "c_a_treasure_sac | TokenTapped$ True"),
    ("creature", "Types:Creature\nT:Mode$ ChangesZone | Origin$ Battlefield | Destination$ "
     "Graveyard | ValidCard$ Card.Self | Execute$ TrigToken\nSVar:TrigToken:DB$ Token | "
     "TokenScript$ c_a_treasure_sac"),
    ("creature", "Types:Creature\n" + ENTERS + "SVar:TrigToken:DB$ Token | TokenScript$ "
     "c_a_treasure_sac | SubAbility$ DBLose\nSVar:DBLose:DB$ LoseLife | LifeAmount$ 1 "
     "| Defined$ You"),
])
def test_a_treasure_the_engine_would_play_otherwise_is_not_taken(kind, script):
    assert treasure_of(kind, script).treasures == 0
