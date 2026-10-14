"""Which cards may lead a Commander deck, and which pairs (P20 F2, `cards.commanders`).

Lauf 1 found a deck page that offered every card as its commander - Bayou
among them, pre-selected for whoever only clicked "Save" - and an importer
that set none when a file named two partners. The wording of every card
here is the catalogue's own (checked 2026-10-13).
"""

import uuid

import pytest

from cards import commanders
from cards.models import OracleCard

PARTNER = "Partner (You can have two commanders if both have partner.)"


def card(name, type_line, text="", keywords=()):
    return OracleCard(oracle_id=uuid.uuid4(), name=name, type_line=type_line,
                      oracle_text=text, keywords=list(keywords))


KRARK = card("Krark, the Thumbless", "Legendary Creature — Goblin Wizard", PARTNER,
             ["Partner"])
THRASIOS = card("Thrasios, Triton Hero", "Legendary Creature — Merfolk Wizard",
                "{4}: Scry 1, then reveal the top card of your library.\n" + PARTNER,
                ["Partner", "Scry"])
PIR = card("Pir, Imaginative Rascal", "Legendary Creature — Human",
           "Partner with Toothy, Imaginary Friend (When this creature enters, target player "
           "may put Toothy into their hand from their library, then shuffle.)\nIf one or more "
           "counters would be put on a permanent your team controls, that many plus one are "
           "put on it instead.", ["Partner with", "Partner"])
TOOTHY = card("Toothy, Imaginary Friend", "Legendary Creature — Illusion",
              "Partner with Pir, Imaginative Rascal (When this creature enters, target player "
              "may put Pir into their hand from their library, then shuffle.)",
              ["Partner with", "Partner"])
WILSON = card("Wilson, Refined Grizzly", "Legendary Creature — Bear Warrior",
              "Choose a Background (You can have a Background as a second commander.)\nReach, "
              "trample, ward {2}", ["Choose a background"])
ACOLYTE = card("Acolyte of Bahamut", "Legendary Enchantment — Background",
               "Commander creatures you own have \"The first Dragon spell you cast each turn "
               "costs {2} less to cast.\"")
ROSE = card("Rose Tyler", "Legendary Creature — Human",
            "Rose Tyler gets +1/+1 for each time counter on it.\nDoctor's companion (You can "
            "have two commanders if the other is the Doctor.)", ["Doctor's companion"])
DOCTOR = card("The Tenth Doctor", "Legendary Creature — Time Lord Doctor", "Allons-y! — ...")
MISSY = card("Missy", "Legendary Creature — Time Lord Rogue", "...")
ABBY = card("Abby, Merciless Soldier", "Legendary Creature — Human Soldier",
            "Partner—Survivors (You can have two commanders if both have this ability.)",
            ["Partner"])
JOEL = card("Joel, Resolute Survivor", "Legendary Creature — Human Survivor",
            "Partner—Survivors (You can have two commanders if both have this ability.)",
            ["Partner"])
ELEVEN = card("Eleven, the Mage", "Legendary Creature — Human Wizard",
              "Partner—Friends forever (You can have two commanders if both have this "
              "ability.)", ["Partner"])
BAYOU = card("Bayou", "Land — Swamp Forest", "({T}: Add {B} or {G}.)")
SOL_RING = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.")
ESIKA = card("Esika, God of the Tree // The Prismatic Bridge",
             "Legendary Creature — God // Legendary Enchantment",
             "Vigilance\n//\nAt the beginning of your upkeep, reveal cards ...")
SQUIRE = card("Faithful Squire // Kaiso, Memory of Loyalty",
              "Creature — Human Soldier // Legendary Creature — Spirit",
              "Whenever you cast a Spirit or Arcane spell ...\n//\n...")
GRIST = card("Grist, the Hunger Tide", "Legendary Planeswalker — Grist",
             "As long as Grist isn't on the battlefield, it's a 1/1 Insect creature in "
             "addition to its other types.\n+1: Create a 1/1 black and green Insect ...")
TEFERI = card("Teferi, Temporal Archmage", "Legendary Planeswalker — Teferi",
              "+1: Look at the top two cards ...\nTeferi, Temporal Archmage can be your "
              "commander.")
GLEEMAX = card("Gleemax", "Legendary Artifact", "You choose all targets for all spells ...")


# --- one commander ---------------------------------------------------------


@pytest.mark.parametrize("candidate", [KRARK, PIR, WILSON, ROSE, DOCTOR, ESIKA, GRIST, TEFERI])
def test_a_legendary_creature_or_a_card_that_says_so_stands_alone(candidate):
    assert commanders.stands_alone(candidate)
    assert commanders.can_be_commander(candidate)


@pytest.mark.parametrize("candidate", [BAYOU, SOL_RING, GLEEMAX, SQUIRE])
def test_a_land_an_artifact_or_a_creature_on_the_back_face_does_not(candidate):
    """Lauf 1 saved Bayou. A creature on an MDFC's back makes no commander."""
    assert not commanders.stands_alone(candidate)
    assert not commanders.can_be_commander(candidate)


def test_a_background_is_a_commander_only_as_half_of_a_pair():
    assert commanders.is_background(ACOLYTE)
    assert commanders.can_be_commander(ACOLYTE)
    assert not commanders.stands_alone(ACOLYTE)


# --- two commanders --------------------------------------------------------


@pytest.mark.parametrize(("first", "second"), [
    (KRARK, THRASIOS),        # plain Partner
    (PIR, TOOTHY),            # Partner with, each naming the other
    (ABBY, JOEL),             # the same Partner—<group>
    (WILSON, ACOLYTE),        # Choose a Background
    (ROSE, DOCTOR),           # Doctor's companion
])
def test_the_five_kinds_of_pair_in_either_order(first, second):
    assert commanders.pair_ok(first, second)
    assert commanders.pair_ok(second, first)


@pytest.mark.parametrize(("first", "second"), [
    (KRARK, KRARK),           # a card does not partner itself
    (PIR, KRARK),             # Partner with names one card only
    (KRARK, ABBY),            # plain and grouped partner do not mix
    (ABBY, ELEVEN),           # nor do two groups
    (KRARK, BAYOU),
    (KRARK, ACOLYTE),         # a Background needs a creature that chooses one
    (ACOLYTE, ACOLYTE),
    (WILSON, KRARK),
    (ROSE, MISSY),            # a Time Lord, not the Doctor
    (KRARK, SOL_RING),
    (KRARK, None),
])
def test_everything_else_is_not_a_pair(first, second):
    assert not commanders.pair_ok(first, second)
    if second is not None:
        assert not commanders.pair_ok(second, first)


def test_partners_for_lists_only_the_cards_that_pair():
    deck = [THRASIOS, PIR, TOOTHY, BAYOU, ACOLYTE, ABBY]
    assert commanders.partners_for(KRARK, deck) == [THRASIOS]
    assert commanders.partners_for(PIR, deck) == [TOOTHY]
    assert commanders.partners_for(WILSON, deck) == [ACOLYTE]
    assert commanders.partners_for(ESIKA, deck) == []


def test_can_pair_says_whether_a_second_field_is_worth_showing():
    assert commanders.can_pair(KRARK)
    assert commanders.can_pair(ACOLYTE)
    assert not commanders.can_pair(ESIKA)
    assert not commanders.can_pair(BAYOU)
