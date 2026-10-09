"""P19 R11 (engine version 15): mana that counts the board.

Gaea's Cradle, Circle of Dreams Druid, Elvish Archdruid, Priest of Titania,
Cabal Stronghold, Crypt of Agadeem, Nykthos and the three Tron lands make an
amount that depends on what else is on the battlefield (or in the graveyard).
The reader names what is counted; the game counts it every turn and taps for
the counting ability only when it beats the plain one beside it.
"""

import random

import pytest

from cards.profiles import _counting_rule
from simulation.cards import COUNTS, CREATURE, FLAT, LAND, Card, ManaAbility
from simulation.game import Game
from simulation.manacost import parse

# --- the reader -----------------------------------------------------------------


@pytest.mark.parametrize("text, subtype, color, activation", [
    ("{T}: Add {G} for each creature you control.", "creature", "G", 0),
    ("Other Elf creatures you control get +1/+1.\n{T}: Add {G} for each Elf you control.",
     "elf", "G", 0),
    ("{T}: Add {G} for each Elf on the battlefield.", "elf", "G", 0),
    ("{T}: Add {C}.\n{3}, {T}: Add {B} for each basic Swamp you control.", "basic:swamp",
     "B", 3),
    ("This land enters tapped.\n{T}: Add {B}.\n{2}, {T}: Add {B} for each black creature "
     "card in your graveyard.", "graveyard:B", "B", 2),
    ("{T}: Add {C}.\n{2}, {T}: Choose a color. Add an amount of mana of that color equal to "
     "your devotion to that color. (Your devotion to a color is the number of mana symbols "
     "of that color in the mana costs of permanents you control.)", "devotion", "", 2),
])
def test_what_is_counted_is_read(text, subtype, color, activation):
    rule = _counting_rule(text)
    assert (rule["rule"], rule["subtype"], rule["color"], rule["activation"]) == (
        "counts", subtype, color, activation)


def test_tron_needs_the_other_two():
    rule = _counting_rule("{T}: Add {C}. If you control an Urza's Mine and an Urza's "
                          "Power-Plant, add {C}{C}{C} instead.")
    assert rule["subtype"] == "names:Urza's Mine|Urza's Power Plant"
    assert rule["produces"] == {"C": 3}


def test_a_count_of_somebody_elses_board_is_not_read():
    assert _counting_rule("Add {R} for each tapped land your opponents control.") is None


# --- the engine -----------------------------------------------------------------


def counting(name, subtype, color="G", activation=0, kind=LAND, plain=None, **extra):
    abilities = [ManaAbility(COUNTS, subtype=subtype, color=color,
                             activation_generic=activation, **extra)]
    if plain:
        abilities.append(ManaAbility(FLAT, plain))
    return Card(name, 0, 0, 0, kind, mana_abilities=tuple(abilities))


def creature(name, *types, cost="{1}{G}"):
    return Card(name, 2, 0, 1, CREATURE, cost=parse(cost), types=frozenset({"creature"}),
                creature_types=frozenset(types))


def game():
    return Game(random.Random(1))


def test_cradle_makes_one_green_for_each_creature():
    cradle = counting("Gaea's Cradle", "creature")
    board = game()
    board.lands = [cradle]
    assert board.mana_ability(cradle) is None, "no creatures: nothing"
    board.creatures = [creature("Bear"), creature("Elf", "elf"), creature("Wolf")]
    assert dict(board.mana_ability(cradle).produces) == {"G": 3}


def test_archdruid_counts_elves_itself_included():
    archdruid = counting("Elvish Archdruid", "elf", kind=CREATURE)
    archdruid = Card(archdruid.name, 3, 0, 1, CREATURE, mana_abilities=archdruid.mana_abilities,
                     creature_types=frozenset({"elf", "druid"}))
    board = game()
    board.creatures = [archdruid, creature("Llanowar Elves", "elf", "druid"), creature("Bear")]
    assert dict(board.mana_ability(archdruid).produces) == {"G": 2}


def test_stronghold_taps_for_colourless_until_its_count_pays():
    swamp = Card("Swamp", 0, 0, 0, LAND, subtypes=frozenset({"swamp"}), basic=True)
    stronghold = counting("Cabal Stronghold", "basic:swamp", color="B", activation=3,
                          plain={"C": 1})
    board = game()
    board.lands = [stronghold, swamp, swamp, swamp]
    assert dict(board.mana_ability(stronghold).produces) == {"C": 1}, "3 - 3 is nothing"
    board.lands += [swamp, swamp]
    ability = board.mana_ability(stronghold)
    assert dict(ability.produces) == {"B": 5} and ability.activation_generic == 3


def test_tron_makes_seven_together():
    mine = counting("Urza's Mine", "names:Urza's Power Plant|Urza's Tower", color="C",
                    produces={"C": 2}, plain={"C": 1})
    plant = counting("Urza's Power Plant", "names:Urza's Mine|Urza's Tower", color="C",
                     produces={"C": 2}, plain={"C": 1})
    tower = counting("Urza's Tower", "names:Urza's Mine|Urza's Power Plant", color="C",
                     produces={"C": 3}, plain={"C": 1})
    board = game()
    board.lands = [mine, plant]
    assert dict(board.mana_ability(mine).produces) == {"C": 1}
    board.lands.append(tower)
    assert sum(board.mana_ability(land).total for land in board.lands) == 7


def test_nykthos_takes_the_colour_with_most_devotion():
    nykthos = counting("Nykthos, Shrine to Nyx", "devotion", color="", activation=2,
                       plain={"C": 1})
    board = game()
    board.lands = [nykthos]
    board.creatures = [creature("Leatherback Baloth", cost="{G}{G}{G}"),
                       creature("Kitchen Finks", cost="{1}{G/W}{G/W}"),
                       creature("Bear", cost="{1}{W}")]
    ability = board.mana_ability(nykthos)
    assert dict(ability.produces) == {"G": 5} and ability.activation_generic == 2


def test_agadeem_counts_black_creature_cards_in_the_graveyard():
    agadeem = counting("Crypt of Agadeem", "graveyard:B", color="B", activation=2,
                       plain={"B": 1})
    board = game()
    board.lands = [agadeem]
    board.graveyard = [creature("Zombie", cost="{1}{B}")] * 4 + [creature("Bear")]
    assert dict(board.mana_ability(agadeem).produces) == {"B": 4}
