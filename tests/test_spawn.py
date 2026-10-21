"""Engine v33: Eldrazi Spawn and Scion made as the card arrives.

Kozilek's Predator, Skittering Invasion and Spawn-Gang Commander make tokens
sacrificed for {C}. Until v33 only a trigger's Spawn was played (Awakening
Zone); a card that made them as it entered, resolved or was cast said
"only grants a mana ability to another permanent" and played without them.

What these tests hold:

1. **Forge's blocks give the count**: a token script of a Spawn or a Scion is
   taken like a Treasure's, marked ``spawn``; a card making both kinds, or
   making them at a moment the engine does not play, is not.
2. **The reader reads a trigger's Scions**, more than one, in each wording.
3. **The engine makes {C}**, and the card page says Eldrazi Spawn.
"""

import random
from types import SimpleNamespace

import pytest

from cards import forge_merge
from cards.models import DerivedProfile, OracleCard
from simulation import build
from simulation.cards import CREATURE, SORCERY
from simulation.game import Game
from simulations import editor
from simulations.engine import adapter
from tests.test_forge_merge import ENTERS, blocks_of
from tests.test_triggers import read

GRANTS = "only grants a mana ability to another permanent"
SPAWN = "c_0_1_eldrazi_spawn_sac"
SCION = "c_1_1_eldrazi_scion_sac"
CAST = "T:Mode$ SpellCast | ValidCard$ Card.Self | Execute$ TrigToken\n"


def made_by(kind: str, script: str, text: str = "") -> DerivedProfile:
    profile = DerivedProfile(kind=kind, treasures=0, source_map={},
                             review_reasons=[GRANTS], needs_review=True)
    forge_merge.treasures(profile, blocks_of(f"Name:Test\n{script}"),
                          OracleCard(name="Test", oracle_text=text))
    return profile


# --- Forge's blocks ------------------------------------------------------------------


@pytest.mark.parametrize("kind, script, count", [
    ("creature", "Types:Creature\n" + ENTERS
     + f"SVar:TrigToken:DB$ Token | TokenScript$ {SPAWN} | TokenAmount$ 2\n", 2),
    ("sorcery", f"Types:Sorcery\nA:SP$ Token | TokenScript$ {SPAWN} | TokenAmount$ 5\n", 5),
    ("creature", "Types:Creature\n" + CAST
     + f"SVar:TrigToken:DB$ Token | TokenScript$ {SCION} | TokenAmount$ 3\n", 3),
])
def test_a_spawn_made_as_the_card_arrives_is_taken(kind, script, count):
    profile = made_by(kind, script, 'When this creature enters, create two 0/1 colorless '
                                    'Eldrazi Spawn creature tokens with "Sacrifice this '
                                    'token: Add {C}."')
    assert (profile.treasures, profile.spawn) == (count, True)
    assert profile.source_map == {"treasures": "forge", "spawn": "forge"}
    assert not profile.needs_review


def test_a_spawn_beside_another_granted_ability_keeps_its_reason():
    profile = made_by("enchantment", "Types:Enchantment\n" + ENTERS
                      + f"SVar:TrigToken:DB$ Token | TokenScript$ {SPAWN} | TokenAmount$ 2\n",
                      'When this enchantment enters, create two 0/1 colorless Eldrazi Spawn '
                      'creature tokens with "Sacrifice this token: Add {C}."\nEldrazi you '
                      'control have "{T}: Add one mana of any color."')
    assert profile.treasures == 2 and profile.review_reasons == [GRANTS]


def test_a_card_making_treasure_and_spawn_is_not_taken():
    profile = made_by("sorcery", "Types:Sorcery\nA:SP$ Token | TokenScript$ c_a_treasure_sac "
                      f"| SubAbility$ DBSpawn\nSVar:DBSpawn:DB$ Token | TokenScript$ {SPAWN}\n")
    assert (profile.treasures, profile.spawn) == (0, False)


def test_a_spawn_made_as_the_card_dies_is_not_taken():
    profile = made_by("creature", "Types:Creature\nT:Mode$ ChangesZone | Origin$ Battlefield "
                      "| Destination$ Graveyard | ValidCard$ Card.Self | Execute$ TrigToken\n"
                      f"SVar:TrigToken:DB$ Token | TokenScript$ {SCION}\n")
    assert profile.treasures == 0


# --- the reader -------------------------------------------------------------------------


@pytest.mark.parametrize("text, count", [
    ("At the beginning of your upkeep, create a 1/1 colorless Eldrazi Scion creature token. "
     "It has \"Sacrifice this creature: Add {C}.\"", 1),
    ("At the beginning of your upkeep, create two 1/1 colorless Eldrazi Scion creature tokens. "
     "They have \"Sacrifice this token: Add {C}.\"", 2),
])
def test_a_trigger_s_scions_are_read(text, count):
    assert read(text).found == [{"event": "upkeep", "filter": "", "treasures": count,
                                 "spawn": True}]


# --- the engine and the card page ---------------------------------------------------------


def test_the_adapter_makes_a_spawn_colourless():
    profile = SimpleNamespace(treasures=2, spawn=True)
    assert adapter._treasure_mana(profile, frozenset({"G"})) == "C"
    assert adapter._treasure_mana(SimpleNamespace(treasures=2), frozenset({"G"})) == "G"
    assert adapter._treasure_mana(SimpleNamespace(treasures=0), frozenset({"G"})) == ""


def test_a_spawn_adds_colourless_as_the_card_resolves():
    invasion = build.card("Skittering Invasion", 8, 0, 8, SORCERY, treasures=5,
                          treasure_mana="C")
    game = Game(random.Random(1))
    game.make_treasures(invasion)
    assert game.treasures == ["C"] * 5


def test_the_card_page_says_eldrazi_spawn():
    predator = build.card("Kozilek's Predator", 4, 0, 4, CREATURE, treasures=2,
                          treasure_mana="C")
    assert editor._text("treasures", SimpleNamespace(card=predator)) == (
        "2 Eldrazi Spawn as it resolves (one {C} each)")
    treasure = build.card("Seize the Spoils", 3, 0, 3, SORCERY, treasures=1, treasure_mana="R")
    assert editor._text("treasures", SimpleNamespace(card=treasure)) == (
        "1 Treasure as it resolves")
