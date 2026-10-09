"""P19 R12: mana that is not this card's to make is not a gap.

An Offer You Can't Refuse gives its Treasures to the spell's controller;
Imprisoned in the Moon and Vraska turn a target into a land or a Treasure;
Dryad Arbor's "{T}: Add {G}" is the reminder of its Forest type. Scryfall
lists mana for each, and each read as "only grants a mana ability".
"""

import pytest

from cards.models import OracleCard
from cards.profiles import _mana_production


def card(name, type_line, text, produced=("G",)):
    return OracleCard(name=name, front_name=name, type_line=type_line, oracle_text=text,
                      produced_mana=list(produced))


@pytest.mark.parametrize("oracle", [
    card("An Offer You Can't Refuse", "Instant",
         "Counter target noncreature spell. Its controller creates two Treasure tokens. "
         "(They're artifacts with \"{T}, Sacrifice this token: Add one mana of any color.\")"),
    card("Imprisoned in the Moon", "Enchantment — Aura",
         "Enchant creature, land, or planeswalker\nEnchanted permanent is a colorless land "
         "with \"{T}: Add {C}\" and loses all other card types and abilities.", ("C",)),
    card("Vraska, Betrayal's Sting", "Legendary Planeswalker — Vraska",
         "−2: Target creature becomes a Treasure artifact with \"{T}, Sacrifice this "
         "artifact: Add one mana of any color\" and loses all other card types and abilities."),
    card("Dryad Arbor", "Land Creature — Forest Dryad",
         "(This land isn't a spell, it's affected by summoning sickness, and it has "
         "\"{T}: Add {G}.\")"),
])
def test_mana_for_someone_else_or_by_land_type_is_no_gap(oracle):
    reading = _mana_production(oracle)
    assert not reading.produces_mana and not reading.notes


def test_a_card_that_grants_mana_to_your_own_permanents_still_says_so():
    reading = _mana_production(card(
        "Cryptolith Rite", "Enchantment",
        "Creatures you control have \"{T}: Add one mana of any color.\""))
    assert reading.notes == ["only grants a mana ability to another permanent"]
