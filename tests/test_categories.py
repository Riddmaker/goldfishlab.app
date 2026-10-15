"""Card model phase 3: categories from blocks (`cards.categories`).

The scripts are made up for these tests, in the format's shape; none is
copied from Forge's card data. The worked examples are the rebuild doc's.
"""

import csv
import io
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from cards import categories, forge, forge_import, profiles
from cards.models import DerivedProfile, OracleCard
from simulation import analysis


def blocks_of(text: str):
    return forge_import.translate(forge.parse(text))


def derive(text: str, role_tags, kind: str = "enchantment"):
    return categories.derive(kind, blocks_of(text), role_tags)


CULTIVATE = """Name:Cultivate
ManaCost:2 G
Types:Sorcery
A:SP$ ChangeZone | Origin$ Library | Destination$ Library | ChangeType$ Land.Basic \
| ChangeNum$ 2 | RememberChanged$ True | SubAbility$ DBField
SVar:DBField:DB$ ChangeZone | Origin$ Library | Destination$ Battlefield \
| ChangeType$ Land.IsRemembered | Tapped$ True | SubAbility$ DBHand
SVar:DBHand:DB$ ChangeZone | Origin$ Library | Destination$ Hand | ChangeType$ Land.IsRemembered
"""

ASHNODS_ALTAR = """Name:Ashnod's Altar
ManaCost:3
Types:Artifact
A:AB$ Mana | Cost$ Sac<1/Creature> | Produced$ C | Amount$ 2
"""

VAMPIRIC_RITES = """Name:Vampiric Rites
ManaCost:B
Types:Enchantment
A:AB$ GainLife | Cost$ 1 B Sac<1/Creature> | Defined$ You | LifeAmount$ 1 | SubAbility$ DBDraw
SVar:DBDraw:DB$ Draw | NumCards$ 1
"""

BLOOD_ARTIST = """Name:Blood Artist
ManaCost:1 B
Types:Creature Vampire
PT:0/1
T:Mode$ ChangesZone | Origin$ Battlefield | Destination$ Graveyard | ValidCard$ Creature.Other \
| TriggerZones$ Battlefield | Execute$ TrigDrain
SVar:TrigDrain:DB$ LoseLife | ValidTgts$ Player | LifeAmount$ 1 | SubAbility$ DBGain
SVar:DBGain:DB$ GainLife | Defined$ You | LifeAmount$ 1
"""

SMOTHERING_TITHE = """Name:Smothering Tithe
ManaCost:3 W
Types:Enchantment
T:Mode$ Drawn | ValidCard$ Card.OppOwn | TriggerZones$ Battlefield | Execute$ TrigToken
SVar:TrigToken:DB$ Token | TokenScript$ c_a_treasure_sac | UnlessCost$ 2 \
| UnlessPayer$ TriggeredCardController
"""

RHYSTIC_STUDY = """Name:Rhystic Study
ManaCost:2 U
Types:Enchantment
T:Mode$ SpellCast | ValidActivatingPlayer$ Opponent | TriggerZones$ Battlefield \
| Execute$ TrigDraw
SVar:TrigDraw:DB$ Draw | Defined$ You | NumCards$ 1 | UnlessCost$ 1 \
| UnlessPayer$ TriggeredActivator | OptionalDecider$ You
"""

PATH_TO_EXILE = """Name:Path to Exile
ManaCost:W
Types:Instant
A:SP$ ChangeZone | ValidTgts$ Creature | Origin$ Battlefield | Destination$ Exile \
| SubAbility$ DBSearch
SVar:DBSearch:DB$ ChangeZone | Origin$ Library | Destination$ Battlefield \
| ChangeType$ Land.Basic | DefinedPlayer$ TargetedController | Tapped$ True | Optional$ True
"""

DEADLY_DISPUTE = """Name:Deadly Dispute
ManaCost:1 B
Types:Instant
A:SP$ Draw | Cost$ 1 B Sac<1/Artifact;Creature/artifact or creature> | NumCards$ 2 \
| SubAbility$ DBTreasure
SVar:DBTreasure:DB$ Token | TokenScript$ c_a_treasure_sac
"""


# --- the worked examples ----------------------------------------------------------


def test_cultivate_is_ramp_and_a_land_search_not_a_tutor():
    found = derive(CULTIVATE, ["ramp"], kind="sorcery")
    assert (found.primary, found.secondary) == ("ramp", "")
    assert found.mechanics == ("land_search",)
    assert found.tempo == {"resolves:0": {"rate": "one_shot", "needs": "none"}}
    assert found.review == ()


def test_ashnods_altar_is_ramp_and_a_free_repeatable_sacrifice_outlet():
    found = derive(ASHNODS_ALTAR, ["mana_rock", "ramp", "sac_outlet"], kind="rock")
    assert found.primary == "ramp"
    assert found.mechanics == ("sac_outlet",)
    # Its mana needs fodder.
    assert found.tempo == {"mana:0": {"rate": "repeatable", "needs": "board"}}


def test_vampiric_rites_is_draw_with_both_strategies_it_feeds():
    found = derive(VAMPIRIC_RITES, ["draw", "draw_engine", "sac_outlet"])
    assert (found.primary, found.secondary) == ("draw", "")
    # The double category asked for: today it carries no lifegain at all.
    assert found.mechanics == ("sac_outlet", "lifegain")
    assert found.tempo["activated:0"] == {"rate": "repeatable", "needs": "board"}


def test_blood_artist_is_a_plan_card_with_no_role():
    found = derive(BLOOD_ARTIST, ["creature", "drain_payoff"], kind="creature")
    assert found.roles == ()
    assert found.mechanics == ("drain_payoff", "lifegain", "death_trigger")
    assert found.tempo["triggered:0"]["rate"] == categories.EVENT
    assert categories.EVENT in categories.REPEATS


def test_smothering_tithe_is_ramp_with_treasure_and_tax_and_never_draw():
    found = derive(SMOTHERING_TITHE, ["gamechanger", "ramp"])
    assert (found.primary, found.secondary) == ("ramp", "")
    assert "draw" not in found.keys
    assert found.mechanics == ("treasure", "tax")
    assert found.tempo["triggered:0"] == {"rate": "event", "needs": "opponent"}


def test_rhystic_study_is_draw_and_tax():
    found = derive(RHYSTIC_STUDY, ["draw", "draw_engine", "gamechanger"])
    assert (found.primary, found.mechanics) == ("draw", ("tax",))
    assert found.tempo["triggered:0"]["needs"] == "opponent"


def test_path_to_exile_gives_the_land_away_and_is_no_ramp():
    found = derive(PATH_TO_EXILE, ["ramp", "removal"], kind="instant")
    assert found.roles == ("removal",)
    # The opponent's land search is no mechanic of the card either.
    assert found.mechanics == ()
    assert found.review == ("dropped:ramp",)


def test_deadly_disputes_treasure_is_a_mechanic_not_ramp():
    found = derive(DEADLY_DISPUTE, ["draw", "ramp"], kind="instant")
    assert found.roles == ("draw",)
    assert found.mechanics == ("treasure",)
    assert found.review == ("dropped:ramp",)
    # The sacrifice is a cost: no sacrifice outlet, and no tempo of its own.
    assert "sac_outlet" not in found.mechanics
    assert list(found.tempo) == ["resolves:0"]


def test_a_card_read_in_part_keeps_the_tag_role_and_says_so():
    found = derive(DEADLY_DISPUTE + "A:AB$ NeverSeenApi | Cost$ T\n", ["draw", "ramp"],
                   kind="instant")
    assert (found.primary, found.secondary) == ("draw", "ramp")
    assert found.review == ("incidental:ramp",)


# --- the roles ----------------------------------------------------------------------


def test_the_tags_stay_the_default_and_ties_go_to_the_specific_role():
    found = categories.derive("instant", (), ["draw", "removal", "ramp", "creature"])
    # One primary, one secondary: the third is left out.
    assert (found.primary, found.secondary) == ("removal", "ramp")
    assert categories.ROLE_ORDER.index("wipe") == 0


def test_a_role_both_the_tags_and_a_block_say_comes_first():
    found = derive(DEADLY_DISPUTE, ["draw", "removal"], kind="instant")
    # Removal comes before draw in the order; but only draw has a block too.
    assert (found.primary, found.secondary) == ("draw", "removal")


def test_a_block_adds_a_role_the_tags_miss():
    # An exile says what it takes; a destroy does not (`CONFIRMS`).
    found = derive("""Name:Test Warden
ManaCost:2 W
Types:Creature Human
PT:1/1
T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self \
| Execute$ TrigExile
SVar:TrigExile:DB$ ChangeZone | ValidTgts$ Creature.OppCtrl | Origin$ Battlefield \
| Destination$ Exile
""", ["creature"], kind="creature")
    assert found.roles == ("removal",)
    assert found.tempo["triggered:0"]["rate"] == categories.ONE_SHOT


def test_blinking_your_own_creature_is_no_removal():
    found = derive("""Name:Test Blink
ManaCost:W
Types:Instant
A:SP$ ChangeZone | ValidTgts$ Creature.YouCtrl | Origin$ Battlefield | Destination$ Exile
""", [], kind="instant")
    assert found.roles == ()


@pytest.mark.parametrize("script, kind", [
    ("""Name:Test Fetch
Types:Land
A:AB$ ChangeZone | Cost$ T Sac<1/CARDNAME> | Origin$ Library | Destination$ Battlefield \
| ChangeType$ Land.Basic
""", "land"),
    ("""Name:Test Rotation
ManaCost:G
Types:Instant
A:SP$ ChangeZone | Cost$ G Sac<1/Land> | Origin$ Library | Destination$ Battlefield \
| ChangeType$ Land
""", "instant"),
])
def test_a_land_fetched_for_one_given_up_is_no_ramp(script, kind):
    found = derive(script, [], kind=kind)
    assert found.roles == ()
    assert found.mechanics == ("land_search",)


def test_a_lands_own_mana_and_a_filter_are_no_ramp_and_a_signet_is():
    land = derive("Name:Test Land\nTypes:Land\nA:AB$ Mana | Cost$ T | Produced$ G\n", [],
                  kind="land")
    filter_ = derive("Name:Test Filter\nManaCost:1\nTypes:Artifact\n"
                     "A:AB$ Mana | Cost$ 1 T | Produced$ Any\n", [], kind="artifact")
    signet = derive("Name:Test Signet\nManaCost:2\nTypes:Artifact\n"
                    "A:AB$ Mana | Cost$ 1 T | Produced$ W U\n", [], kind="rock")
    assert land.roles == filter_.roles == ()
    assert signet.roles == ("ramp",)


def test_a_wipe_sweeps_what_others_have():
    exile = derive("Name:Test Exile\nManaCost:2 W W\nTypes:Sorcery\n"
                   "A:SP$ ChangeZoneAll | Origin$ Battlefield | Destination$ Exile "
                   "| ChangeType$ Creature\n", [], kind="sorcery")
    tidy = derive("Name:Test Tidy\nManaCost:2\nTypes:Artifact\n"
                  "A:AB$ ChangeZoneAll | Cost$ T | Origin$ Battlefield | Destination$ Exile "
                  "| ChangeType$ Nightmare\n", [], kind="artifact")
    assert exile.roles == ("wipe",)
    assert tidy.roles == ()


WRATH = "Name:Test Wrath\nManaCost:2 W W\nTypes:Sorcery\nA:SP$ DestroyAll | ValidCards$ Creature\n"
SPIDERS = ("Name:Test Spiders\nManaCost:5 G G\nTypes:Instant\n"
           "A:SP$ Destroy | ValidTgts$ Creature.withFlying | SubAbility$ DBGear\n"
           "SVar:DBGear:DB$ DestroyAll | ValidCards$ Equipment.Attached\n")


def test_a_block_that_cannot_say_whom_it_hits_only_backs_a_tag_up():
    # The block keeps "destroy all" but not what: a wrath and "all Equipment
    # attached to it" read alike, so only the tag makes a wipe.
    assert derive(WRATH, ["wipe"], kind="sorcery").roles == ("wipe",)
    assert derive(WRATH, [], kind="sorcery").roles == ()
    assert derive(SPIDERS, ["removal"], kind="instant").roles == ("removal",)
    spike = derive("Name:Test Spike\nManaCost:R\nTypes:Sorcery\n"
                   "A:SP$ DealDamage | ValidTgts$ Player | NumDmg$ 3\n", [], kind="sorcery")
    gift = derive("Name:Test Gift\nManaCost:2\nTypes:Sorcery\n"
                  "A:SP$ Draw | ValidTgts$ Opponent | NumCards$ 2\n", [], kind="sorcery")
    assert spike.roles == gift.roles == ()
    # Backed by the tag, the block still counts: removal both say beats
    # ramp only the tags say.
    bolt = derive("Name:Test Bolt\nManaCost:R\nTypes:Instant\n"
                  "A:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3\n", ["ramp", "removal"],
                  kind="instant")
    assert bolt.primary == "removal"


def test_a_ward_is_no_counterspell_and_a_blink_no_removal():
    titan = derive("Name:Test Titan\nManaCost:4 U U\nTypes:Creature Giant\nPT:6/6\n"
                   "T:Mode$ BecomesTarget | ValidTarget$ Card.Self | ValidSource$ Card.OppCtrl "
                   "| TriggerZones$ Battlefield | Execute$ TrigCounter\n"
                   "SVar:TrigCounter:DB$ Counter | Defined$ TriggeredSourceSA | UnlessCost$ 2\n",
                   [], kind="creature")
    blink = derive("Name:Test Blink\nManaCost:2\nTypes:Creature Eldrazi\nPT:2/2\n"
                   "A:AB$ ChangeZone | Cost$ 2 | ValidTgts$ Creature.Other | Origin$ Battlefield "
                   "| Destination$ Exile | RememberChanged$ True | SubAbility$ DBReturn\n"
                   "SVar:DBReturn:DB$ ChangeZone | Defined$ Remembered | Origin$ Exile "
                   "| Destination$ Battlefield\n", [], kind="creature")
    assert titan.roles == blink.roles == ()


def test_a_search_for_a_basic_or_a_desert_is_a_land_search():
    frontier = derive("Name:Test Frontier\nManaCost:2 R\nTypes:Sorcery\n"
                      "A:SP$ ChangeZone | Origin$ Library | Destination$ Battlefield "
                      "| ChangeType$ Land.Basic,Desert | ChangeNum$ 2 | Tapped$ True\n",
                      [], kind="sorcery")
    assert frontier.roles == ("ramp",)
    assert "land_search" in frontier.mechanics


# --- the mechanics ------------------------------------------------------------------


def test_returning_itself_is_self_recursion_and_returning_another_is_reanimation():
    itself = derive("""Name:Test Ghost
ManaCost:1 B
Types:Creature Spirit
PT:2/1
T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Land.YouCtrl \
| TriggerZones$ Graveyard | Execute$ TrigReturn
SVar:TrigReturn:DB$ ChangeZone | Defined$ Self | Origin$ Graveyard | Destination$ Battlefield
""", [], kind="creature")
    another = derive("""Name:Test Necromancy
ManaCost:2 B
Types:Sorcery
A:SP$ ChangeZone | ValidTgts$ Creature | TgtZone$ Graveyard | Origin$ Graveyard \
| Destination$ Battlefield
""", [], kind="sorcery")
    assert (itself.roles, itself.mechanics) == ((), ("recursive",))
    assert another.roles == ("recursion",)
    assert another.mechanics == ("reanimate",)


@pytest.mark.parametrize("token, mechanics", [
    ("w_1_1_soldier", ("tokens",)),
    ("c_a_food_sac", ()),
    ("c_a_treasure_sac", ("treasure",)),
])
def test_tokens_are_creature_tokens_and_treasure_is_its_own(token, mechanics):
    found = derive(f"Name:Test Maker\nManaCost:2\nTypes:Sorcery\n"
                   f"A:SP$ Token | TokenScript$ {token} | TokenAmount$ 2\n", [], kind="sorcery")
    assert found.mechanics == mechanics


def test_a_token_made_for_an_opponent_is_no_mechanic():
    found = derive("""Name:Test Gift
ManaCost:2 G
Types:Instant
A:SP$ Destroy | ValidTgts$ Permanent | SubAbility$ DBToken
SVar:DBToken:DB$ Token | TokenScript$ g_3_3_beast | TokenOwner$ TargetedController
""", ["removal"], kind="instant")
    assert found.roles == ("removal",)
    assert found.mechanics == ()


def test_a_death_trigger_is_about_another_creature_dying():
    own = derive("""Name:Test Own Death
ManaCost:1 B
Types:Creature Zombie
PT:1/1
T:Mode$ ChangesZone | Origin$ Battlefield | Destination$ Graveyard | ValidCard$ Card.Self \
| Execute$ TrigDraw
SVar:TrigDraw:DB$ Draw | Defined$ You | NumCards$ 1
""", [], kind="creature")
    assert "death_trigger" not in own.mechanics
    assert own.tempo["triggered:0"]["rate"] == categories.ONE_SHOT
    assert "death_trigger" in derive(BLOOD_ARTIST, [], kind="creature").mechanics


def test_a_cost_raised_for_everybody_is_tax_and_a_spells_own_is_not():
    thalia = derive("Name:Test Thalia\nManaCost:1 W\nTypes:Creature Human\nPT:2/1\n"
                    "S:Mode$ RaiseCost | ValidCard$ Card.nonCreature | Amount$ 1\n", [],
                    kind="creature")
    tide = derive("Name:Test Tide\nManaCost:X U U\nTypes:Sorcery\n"
                  "S:Mode$ RaiseCost | ValidCard$ Card.Self | Amount$ 1\n", [], kind="sorcery")
    assert thalia.mechanics == ("tax",)
    assert tide.mechanics == ()


def test_lifelink_and_a_life_gained_trigger_are_lifegain():
    lifelink = derive("Name:Test Lifelinker\nManaCost:1 W\nTypes:Creature Cleric\nPT:2/2\n"
                      "K:Lifelink\n", [], kind="creature")
    payoff = derive("""Name:Test Payoff
ManaCost:1 W
Types:Creature Cat
PT:2/2
T:Mode$ LifeGained | ValidPlayer$ You | TriggerZones$ Battlefield | Execute$ TrigPump
SVar:TrigPump:DB$ PutCounter | Defined$ Self | CounterType$ P1P1 | CounterNum$ 1
""", [], kind="creature")
    assert lifelink.mechanics == payoff.mechanics == ("lifegain",)
    # A keyword alone says nothing about tempo.
    assert lifelink.tempo == {}


def test_the_strategy_tags_stay_mechanics():
    found = categories.derive("creature", (), ["creature", "evasion", "steal", "draw_engine"])
    assert found.mechanics == ("steal", "evasion")


# --- without blocks ------------------------------------------------------------------


def test_a_card_without_blocks_is_read_off_the_readers_fields():
    facts = SimpleNamespace(draws_cards=1, treasures=0, triggers=[{"treasures": 1}],
                            land_search={"battlefield": 1}, opponent_life_loss=2,
                            produces_mana=True)
    found = categories.derive("creature", (), ["creature"], facts)
    assert found.roles == ("ramp", "draw")
    assert found.mechanics == ("drain_payoff", "land_search", "treasure")
    assert found.tempo == {}
    land = SimpleNamespace(produces_mana=True, land_search={"battlefield": 0, "hand": 1})
    assert categories.derive("land", (), [], land).roles == ()


# --- the vocabulary ------------------------------------------------------------------


def test_the_roles_are_the_reports_categories():
    assert set(categories.ROLES) == set(analysis.SEEN_CATEGORIES)
    assert set(categories.ROLE_ORDER) == set(categories.ROLES)
    assert set(analysis.SEEN_STRATEGIES) <= set(categories.MECHANICS)


def test_cheap_is_worked_out_from_the_mana_value():
    assert categories.cheap(2) and not categories.cheap(3)


# --- in the profile ------------------------------------------------------------------


def test_the_profile_holds_the_categories_beside_unchanged_roles(catalogue):
    forge_import.run()
    sol_ring = OracleCard.objects.get(name="Sol Ring")
    without = profiles.derive(sol_ring, forge=())
    profile = profiles.derive(sol_ring)
    assert profile.role_tags == without.role_tags
    assert profile.primary_role == "ramp"
    assert profile.tempo == {"mana:0": {"rate": "repeatable", "needs": "none"}}
    assert profile.categories_version == categories.VERSION


def test_the_rebuild_writes_the_categories(catalogue):
    forge_import.run()
    DerivedProfile.objects.update(primary_role="", categories_version=0)
    profiles.rebuild(OracleCard.objects.filter(name__in=["Sol Ring", "Llanowar Elves"]))
    stored = DerivedProfile.objects.get(oracle_card__name="Llanowar Elves")
    assert (stored.primary_role, stored.categories_version) == ("ramp", categories.VERSION)
    assert stored.tempo == {"mana:0": {"rate": "repeatable", "needs": "none"}}


# --- the gate D report ---------------------------------------------------------------


def test_the_report_counts_today_and_new_without_writing(catalogue, tmp_path):
    forge_import.run()
    DerivedProfile.objects.update(categories_version=0)
    out = io.StringIO()
    path = tmp_path / "categories.csv"
    call_command("categories_report", "--out", str(path), stdout=out)
    text = out.getvalue()
    assert "category" in text and "ramp" in text
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["name"]: row["primary"] for row in rows}["Sol Ring"] == "ramp"
    # Read-only: nothing was stored.
    assert not DerivedProfile.objects.exclude(categories_version=0).exists()


def test_the_report_shows_one_card_and_the_decks(catalogue):
    forge_import.run()
    out = io.StringIO()
    call_command("categories_report", "--cards", "Sol Ring", stdout=out)
    assert out.getvalue().startswith("Sol Ring: today ['ramp']")
    assert "mana:0: repeatable, needs none" in out.getvalue()
    call_command("categories_report", "--decks", stdout=io.StringIO())


def test_the_families_name_each_difference():
    found = derive(PATH_TO_EXILE, ["ramp", "removal"], kind="instant")
    from simulations.management.commands.categories_report import families

    assert families(["ramp", "removal"], found) == {
        "gained": [], "dropped": ["ramp"], "kept": [], "trimmed": [], "mechanic": []}
    many = categories.derive("instant", (), ["draw", "removal", "ramp"])
    assert families(["draw", "removal", "ramp"], many)["trimmed"] == ["draw"]


@pytest.mark.parametrize("what", ["Card.Plains", "EACH Forest & Plains", "Land.Basic"])
def test_a_search_for_basic_land_types_is_a_land_search(what):
    found = derive(f"""Name:Test Knight
ManaCost:W W
Types:Creature Human Knight
PT:2/2
T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self \
| Execute$ TrigSearch
SVar:TrigSearch:DB$ ChangeZone | Origin$ Library | Destination$ Battlefield | ChangeType$ {what}
""", [], kind="creature")
    assert (found.roles, found.mechanics) == (("ramp",), ("land_search",))
