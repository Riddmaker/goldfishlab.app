"""Card model phase 1: Forge's card scripts translated into blocks and stored.

The scripts below are made up for these tests, in the format's shape; none is
copied from Forge's card data.
"""

import pytest

from cards import forge, forge_import, refresh
from cards.models import BulkImport, ForgeScript, OracleCard
from simulation import blocks as b
from tests.conftest import FORGE_COMMIT, FORGE_SCRIPTS


def blocks_of(text: str) -> tuple[b.Ability, ...]:
    return forge_import.translate(forge.parse(text))


def only(abilities, kind):
    found = [ability for ability in abilities if ability.kind == kind]
    assert len(found) == 1, found
    return found[0]


# --- translation ------------------------------------------------------------------


def test_a_pain_land_taps_for_colourless_and_for_either_colour_with_damage():
    found = blocks_of("""Name:Test Wastes
Types:Land
A:AB$ Mana | Cost$ T | Produced$ C
A:AB$ Mana | Cost$ T | Produced$ Combo W U | SubAbility$ DBPain
SVar:DBPain:DB$ DealDamage | NumDmg$ 1 | Defined$ You
""")
    assert [ability.id for ability in found] == ["mana:0", "mana:1"]
    assert found[0].effects == (b.Effect("add_mana", (("color", "C"),)),)
    assert found[0].costs == (b.Cost("tap"),)
    assert [effect.verb for effect in found[1].effects] == ["add_mana", "damage"]
    assert found[1].effects[0].arg("color") == "WU"
    assert found[1].effects[1].args == (("n", 1), ("who", "you"))


def test_a_creature_that_draws_as_it_enters():
    trigger = only(blocks_of("""Name:Test Owl
Types:Creature Bird
K:Flying
T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self \
| Execute$ TrigDraw
SVar:TrigDraw:DB$ Draw | Defined$ You | NumCards$ 1
"""), b.TRIGGERED)
    assert trigger.event == b.Event("enters", who="self")
    assert trigger.effects == (b.Effect("draw", (("n", 1), ("who", "you"))),)


def test_treasure_counted_from_the_board_keeps_the_count():
    trigger = only(blocks_of("""Name:Test Pirate
Types:Creature Goblin
T:Mode$ ChangesZone | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ TrigT
SVar:TrigT:DB$ Token | TokenAmount$ X | TokenScript$ c_a_treasure_sac
SVar:X:Count$Valid Artifact.OppCtrl
"""), b.TRIGGERED)
    assert trigger.effects == (b.Effect("create_token", (("n", "count:Valid"),
                                                         ("token", "treasure"))),)
    assert trigger.unread == ()


def test_entering_tapped_unless_life_is_paid():
    replacement = only(blocks_of("""Name:Test Fountain
Types:Land Plains Island
R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ DBTap
SVar:DBTap:DB$ Tap | ETB$ True | Defined$ Self | UnlessCost$ PayLife<2>
"""), b.REPLACEMENT)
    assert replacement.event == b.Event("enters", who="self")
    assert replacement.effects == (b.Effect("enters_tapped", (("unless", "PayLife<2>"),
                                                              ("who", "self"))),)


def test_a_fetch_land_costs_tap_life_and_itself():
    ability = only(blocks_of("""Name:Test Delta
Types:Land
A:AB$ ChangeZone | Cost$ T PayLife<1> Sac<1/CARDNAME> | Origin$ Library \
| Destination$ Battlefield | ChangeType$ Island,Swamp
"""), b.ACTIVATED)
    assert ability.costs == (b.Cost("tap"), b.Cost("life", amount=1),
                             b.Cost("sacrifice_self", amount=1))
    assert ability.effects[0].args == (("from", "library"), ("to", "battlefield"),
                                       ("what", "Island,Swamp"))


def test_a_spell_that_costs_two_creatures_keeps_the_amount():
    found = blocks_of("""Name:Test Tribute
ManaCost:2 B
Types:Sorcery
A:SP$ Destroy | Cost$ 2 B Sac<2/Creature.Other/two creatures> | ValidTgts$ Artifact
""")
    cast = only(found, b.CAST)
    assert cast.costs == (b.Cost("sacrifice", amount=2, types=frozenset({"creature"}),
                                 other=True),)
    assert only(found, b.RESOLVES).effects == (b.Effect("destroy", (("target", True),)),)


def test_an_activated_cost_keeps_its_mana():
    ability = only(blocks_of("""Name:Test Bird
Types:Creature Bird
A:AB$ Token | Cost$ 1 G T | TokenScript$ c_a_food_sac
"""), b.ACTIVATED)
    assert str(ability.costs[0].mana) == "{1}{G}"
    assert ability.costs[1] == b.Cost("tap")


def test_a_charm_is_one_ability_per_mode():
    found = blocks_of("""Name:Test Charm
ManaCost:W U B
Types:Instant
A:SP$ Charm | Choices$ DBDestroy,DBDraw
SVar:DBDestroy:DB$ Destroy | ValidTgts$ Enchantment
SVar:DBDraw:DB$ Draw | NumCards$ 2
""")
    assert [(ability.mode, [effect.verb for effect in ability.effects])
            for ability in found] == [(0, ["choose"]), (1, ["destroy"]), (2, ["draw"])]
    assert found[0].effects[0].args == (("n", 1), ("of", 2))


def test_a_construct_outside_the_vocabulary_is_named_not_dropped():
    found = blocks_of("""Name:Test Oddity
Types:Artifact
A:AB$ SomethingNew | Cost$ T
T:Mode$ NeverSeen | Execute$ TrigDraw
SVar:TrigDraw:DB$ Draw | NumCards$ 1
K:Rampage:2
A:AB$ Draw | Cost$ Blot<1/Thing> | NumCards$ 1
""")
    assert forge_import.unmodeled(found) == ["AB:SomethingNew", "K:Rampage", "T:NeverSeen",
                                             "cost:Blot"]


def test_a_condition_is_listed_as_unread():
    ability = only(blocks_of("""Name:Test Hoard
Types:Artifact
A:AB$ Draw | Cost$ T | NumCards$ 1 | ConditionCheckSVar$ X | ConditionSVarCompare$ GE3
"""), b.ACTIVATED)
    assert ability.unread == ("cond:ConditionCheckSVar", "cond:ConditionSVarCompare")


def test_keywords_enters_with_counters_and_a_modal_back_face():
    found = blocks_of("""Name:Test Spell
ManaCost:1 B
Types:Instant
K:Flash
K:etbCounter:P1P1:2
A:SP$ LoseLife | Defined$ Opponent | LifeAmount$ 2
AlternateMode:Modal

ALTERNATE

Name:Test Vale
Types:Land
A:AB$ Mana | Cost$ T | Produced$ B
""")
    assert {ability.kind for ability in found} == {b.RESOLVES, b.STATIC, b.REPLACEMENT,
                                                   b.MANA}
    assert only(found, b.STATIC).effects == (b.Effect(b.KEYWORD, (("name", "Flash"),)),)
    assert only(found, b.REPLACEMENT).effects[0].args == (("counter", "P1P1"), ("n", 2),
                                                         ("who", "self"))


def test_blocks_survive_json():
    text = "\n\n".join(FORGE_SCRIPTS.values())
    found = blocks_of(text) + blocks_of("""Name:Test Tribute
Types:Sorcery
A:SP$ Charm | Choices$ DBA
SVar:DBA:DB$ Draw | NumCards$ 1 | ConditionPresent$ Card
T:Mode$ SpellCast | ValidCard$ Instant,Sorcery | ValidActivatingPlayer$ You | Execute$ DBA
""")
    assert b.from_json(b.to_json(found)) == found


def test_the_vocabulary_is_about_250_constructs():
    """An API counts once, whether an ability, a spell or a sub-ability uses it."""
    entries = sum(map(len, (forge_import.VERBS, forge_import.EVENTS, forge_import.STATICS,
                            forge_import.REPLACEMENTS, forge_import.COSTS,
                            forge_import.KEYWORDS)))
    assert 200 <= entries <= 300
    assert {"AB:Mana", "SP:Mana", "T:ChangesZone", "cost:Sac", "K:Flying"} <= set(
        forge_import.vocabulary())


def test_five_goldfish_facts_from_blocks():
    ramp = blocks_of("""Name:Test Growth
ManaCost:1 G
Types:Sorcery
A:SP$ ChangeZone | Origin$ Library | Destination$ Battlefield | ChangeType$ Land.Basic \
| Tapped$ True | SubAbility$ DBDraw
SVar:DBDraw:DB$ Draw | NumCards$ 2 | SubAbility$ DBToken
SVar:DBToken:DB$ Token | TokenAmount$ 1 | TokenScript$ c_a_treasure_sac
""")
    assert forge_import.facts(ramp) == {"taps_for": "", "draws": 2, "land_search": True,
                                        "enters_tapped": False, "treasures": True}
    gate = blocks_of("""Name:Test Gate
Types:Land Forest Island
R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped
SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True
""")
    facts = forge_import.facts(gate, "Land — Forest Island")
    assert (facts["taps_for"], facts["enters_tapped"]) == ("GU", True)
    signet = blocks_of("""Name:Test Signet
Types:Artifact
A:AB$ Mana | Cost$ T | Produced$ Combo ColorIdentity
A:AB$ Mana | Cost$ 1 T | Produced$ Combo W U
""")
    assert forge_import.facts(signet)["taps_for"] == "BGRUW"


def test_taps_for_is_the_mana_tap_makes_net():
    town = blocks_of("""Name:Test Town
Types:Land
A:AB$ Mana | Cost$ T | Produced$ C
A:AB$ Mana | Cost$ 1 T | Produced$ Any
A:AB$ Mana | Cost$ T PayLife<1> | Produced$ G
A:AB$ Mana | Cost$ 1 T | Produced$ R W
""")
    signet = [ability for ability in town if ability.kind == b.MANA][3]
    assert signet.effects[0].args == (("all", True), ("color", "RW"))
    # Not the {1} that converts into any colour; the {1} that makes two does.
    assert forge_import.facts(town, "Land")["taps_for"] == "CGRW"
    cavern = blocks_of("""Name:Test Cavern
Types:Land
A:AB$ Mana | Cost$ T | Produced$ Any | RestrictValid$ Spell.Creature+ChosenType
""")
    # Restricted mana is mana the reader reads too, with the restriction.
    assert only(cavern, b.MANA).effects[0].arg("restrict") == "Spell.Creature+ChosenType"
    assert forge_import.facts(cavern)["taps_for"] == "BGRUW"
    chosen = blocks_of("""Name:Test Apple
Types:Land
A:AB$ Mana | Cost$ T | Produced$ Chosen
""")
    reflected = blocks_of("""Name:Test Mox
Types:Artifact
A:AB$ ManaReflected | Cost$ T | ColorOrType$ Color | ReflectProperty$ Is
""")
    assert forge_import.facts(chosen)["taps_for"] == forge_import.facts(reflected)[
        "taps_for"] == "BGRUW"


def test_a_search_for_an_opponent_is_no_land_search():
    found = blocks_of("""Name:Test Path
Types:Instant
A:SP$ ChangeZone | Origin$ Library | Destination$ Battlefield | ChangeType$ Land.Basic \
| DefinedPlayer$ TargetedController
""")
    assert not forge_import.facts(found)["land_search"]


# --- the pinned pack --------------------------------------------------------------


def test_the_pin_names_the_packed_commit():
    pack = forge.PIN.with_name("forge_scripts.jsonl.gz")
    assert forge.header(pack)["commit"] == forge.pinned()


def test_a_pack_reads_back_its_scripts(_forge_pack):
    assert forge.header(_forge_pack)["commit"] == FORGE_COMMIT
    assert sorted(script.name for script in forge.scripts(_forge_pack)) == [
        "Llanowar Elves", "Not In The Catalogue", "Sol Ring"]


def test_a_pack_wants_a_full_commit_hash(tmp_path):
    with pytest.raises(ValueError):
        forge.pack([], "abc", "2026-10-13T08:00:00+00:00", tmp_path / "x.jsonl.gz")


# --- the run ----------------------------------------------------------------------


def test_the_run_stores_blocks_for_the_cards_in_the_catalogue(catalogue):
    result = forge_import.run()
    assert result.status == BulkImport.Status.OK
    rows = {row.oracle_card.name: row for row in ForgeScript.objects.select_related(
        "oracle_card")}
    assert set(rows) == {"Sol Ring", "Llanowar Elves"}
    sol_ring = b.from_json(rows["Sol Ring"].blocks)
    assert sol_ring[0].effects == (b.Effect("add_mana", (("color", "C"), ("n", 2))),)
    assert rows["Sol Ring"].commit == FORGE_COMMIT
    assert rows["Sol Ring"].unmodeled == []
    record = BulkImport.objects.get(kind=BulkImport.Kind.FORGE_SCRIPTS)
    assert (record.forge_commit, record.rows_seen, record.rows_written,
            record.rows_skipped) == (FORGE_COMMIT, 3, 2, 1)


def test_an_unchanged_pin_is_skipped_and_a_new_translation_is_not(catalogue, monkeypatch):
    forge_import.run()
    assert forge_import.run().skipped
    monkeypatch.setattr(forge_import, "TRANSLATION", forge_import.TRANSLATION + 1)
    assert forge_import.stale()
    assert not forge_import.run().skipped


def test_too_many_failures_keep_the_last_blocks(catalogue, monkeypatch):
    forge_import.run()
    before = list(ForgeScript.objects.values_list("pk", "blocks"))

    def broken(script):
        raise ValueError("unreadable")

    monkeypatch.setattr(forge_import, "translate", broken)
    result = refresh.forge_scripts(force=True)
    assert result.status == BulkImport.Status.FAILED
    assert "2 of 3 scripts failed" in result.message
    assert list(ForgeScript.objects.values_list("pk", "blocks")) == before
    assert BulkImport.objects.filter(kind=BulkImport.Kind.FORGE_SCRIPTS,
                                     status=BulkImport.Status.FAILED).count() == 1


def test_a_card_that_lost_its_script_loses_its_blocks(catalogue, tmp_path, monkeypatch):
    forge_import.run()
    smaller = tmp_path / "smaller.jsonl.gz"
    forge.pack([(path, text) for path, text in FORGE_SCRIPTS.items() if "sol_ring" not in path],
               "e" * 40, "2026-10-14T08:00:00+00:00", smaller)
    monkeypatch.setattr(forge, "PACK", smaller)
    forge_import.run()
    assert list(ForgeScript.objects.values_list("oracle_card__name", flat=True)) == [
        "Llanowar Elves"]


def test_a_catalogue_refresh_translates_the_scripts(catalogue):
    loaded = refresh.load([], source=None)
    assert [result.kind for result in loaded.results] == [BulkImport.Kind.FORGE_SCRIPTS]
    assert ForgeScript.objects.count() == 2
    assert OracleCard.objects.get(name="Sol Ring").forge.path == "s/sol_ring.txt"


# --- the comparison with our reader ------------------------------------------------


def test_facts_are_classified():
    from simulations.management.commands.forge_disagreements import classify

    assert classify("taps_for", "CWU", "WU") == "forge_more"
    assert classify("taps_for", "B", "BGRUW") == "ours_more"
    assert classify("taps_for", "C", "B") == "conflict"
    assert classify("draws", 1, 0) == "forge_more"
    assert classify("draws", "x", 1) == "conflict"
    assert classify("treasures", False, True) == "ours_more"
    assert classify("enters_tapped", True, True) == "agree"


def test_the_disagreement_report_runs(catalogue, tmp_path):
    from io import StringIO

    from django.core.management import call_command

    forge_import.run()
    out, path = StringIO(), tmp_path / "rows.json"
    call_command("forge_disagreements", "--out", str(path), stdout=out)
    assert "2 of " in out.getvalue()
    assert path.exists()
