"""P19 R13 (engine version 17): mana on top of what a source makes.

Wild Growth, Utopia Sprawl and Fertile Ground add to the land they enchant;
Mirari's Wake, Vorinclex, Kinnan, Forsaken Monument, Badgermole Cub and Caged
Sun add to every source of a kind; Mana Reflection and Nyxbloom Ancient
multiply. Cryptolith Rite gives every creature a mana ability, Abundant Growth
the enchanted land. Bloom Tender, Sanctum Weaver and Overgrown Battlement
count the board, and Battle Hymn and High Tide are rituals that do.

Where the pool cannot hold what a player gets - a bonus "of any type that
land produced" on a land that made a choice - colour is given up, never mana
invented.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _mana_rule
from simulation import actions
from simulation.cards import (
    COUNTS,
    CREATURE,
    ENCHANTMENT,
    EXTRA,
    FLAT,
    GRANT,
    LAND,
    MULTIPLY,
    RITUAL,
    ROCK,
    Card,
    DeckDefinition,
    ManaAbility,
)
from simulation.game import Game
from simulation.manacost import parse
from simulations.engine.adapter import _ability_text

Kind = DerivedProfile.Kind

# --- the reader -----------------------------------------------------------------


def rule_of(text, kind=Kind.ENCHANTMENT, name="Card"):
    card = OracleCard(name=name, front_name=name, type_line="Enchantment", oracle_text=text)
    return _mana_rule(card, kind)


@pytest.mark.parametrize("text, kind, expected", [
    ("Enchant land\nWhenever enchanted land is tapped for mana, its controller adds an "
     "additional {G}.", Kind.ENCHANTMENT,
     {"rule": "extra", "subtype": "enchanted", "produces": {"G": 1}, "enchants": "land"}),
    ("Enchant land\nWhenever enchanted land is tapped for mana, its controller adds an "
     "additional {G}{G}.", Kind.ENCHANTMENT, {"subtype": "enchanted", "produces": {"G": 2}}),
    ("Enchant land\nWhenever enchanted land is tapped for mana, its controller adds an "
     "additional one mana of any color.", Kind.ENCHANTMENT, {"produces": {"WUBRG": 1}}),
    ("Enchant land\nWhenever enchanted land is tapped for mana, its controller adds an "
     "additional two mana in any combination of colors.", Kind.ENCHANTMENT,
     {"produces": {"WUBRG": 2}}),
    ("Enchant Forest\nAs this Aura enters, choose a color.\nWhenever enchanted Forest is "
     "tapped for mana, its controller adds an additional one mana of the chosen color.",
     Kind.ENCHANTMENT, {"subtype": "enchanted:forest", "color": "chosen", "enchants": "forest"}),
    ("Creatures you control get +1/+1.\nWhenever you tap a land for mana, add one mana of any "
     "type that land produced.", Kind.ENCHANTMENT, {"rule": "extra", "subtype": "land"}),
    ("Whenever a player taps a land for mana, that player adds one mana of any type that land "
     "produced.", Kind.ENCHANTMENT, {"subtype": "land"}),
    ("Whenever you tap a nonland permanent for mana, add one mana of any type that permanent "
     "produced.", Kind.CREATURE, {"subtype": "nonland"}),
    ("Whenever you tap a creature for mana, add an additional {G}.", Kind.CREATURE,
     {"subtype": "creature", "produces": {"G": 1}}),
    ("Colorless creatures you control get +2/+2.\nWhenever you tap a permanent for {C}, add an "
     "additional {C}.", Kind.ARTIFACT, {"subtype": "colorless", "produces": {"C": 1}}),
    ("As this artifact enters, choose a color.\nWhenever a land's ability causes you to add "
     "one or more mana of the chosen color, add an additional one mana of that color.",
     Kind.ROCK, {"subtype": "chosen_land", "color": "chosen"}),
    ("As this artifact enters, choose a color.\nWhenever a basic land is tapped for mana of "
     "the chosen color, its controller adds an additional one mana of that color.",
     Kind.ARTIFACT, {"subtype": "chosen_basic", "color": "chosen"}),
    ("If you tap a permanent for mana, it produces twice as much of that mana instead.",
     Kind.ENCHANTMENT, {"rule": "multiply", "times": 2}),
    ("If you tap a permanent for mana, it produces three times as much of that mana instead.",
     Kind.CREATURE, {"rule": "multiply", "times": 3}),
    ("Creatures you control have \"{T}: Add one mana of any color.\"", Kind.ENCHANTMENT,
     {"rule": "grant", "subtype": "creature", "produces": {"WUBRG": 1}}),
    ("Creatures you control have \"{T}: Add {G}.\"", Kind.CREATURE,
     {"rule": "grant", "produces": {"G": 1}}),
    ("Enchant land\nWhen this Aura enters, draw a card.\nEnchanted land has \"{T}: Add one "
     "mana of any color.\"", Kind.ENCHANTMENT, {"rule": "grant", "subtype": "enchanted"}),
    ("Vivid — {T}: For each color among permanents you control, add one mana of that color.",
     Kind.CREATURE, {"rule": "counts", "subtype": "colors_among"}),
    ("{T}: Add X mana of any one color, where X is the number of enchantments you control.",
     Kind.CREATURE, {"rule": "counts", "subtype": "enchantment", "color": ""}),
    ("Defender\n{T}: Add X mana in any combination of colors, where X is the number of "
     "creatures you control with defender.", Kind.CREATURE,
     {"subtype": "creature:defender", "color": "WUBRG"}),
    ("Defender\n{T}: Add {G} for each creature you control with defender.", Kind.CREATURE,
     {"subtype": "creature:defender", "color": "G"}),
    ("Add {R} for each creature you control.", Kind.RITUAL,
     {"rule": "ritual", "subtype": "creature", "color": "R"}),
    ("Until end of turn, whenever a player taps an Island for mana, that player adds an "
     "additional {U}.", Kind.RITUAL, {"rule": "ritual", "subtype": "tapped:island",
                                      "color": "U"}),
])
def test_mana_on_top_is_read(text, kind, expected):
    rule = rule_of(text, kind)
    assert rule is not None
    assert {key: rule.get(key) for key in expected} == expected


@pytest.mark.parametrize("text, kind", [
    # The count of Elves on the bonus, a land creature, two of one colour
    # and a restriction: none of them is what the engine plays.
    ("Enchant land\nWhenever enchanted land is tapped for mana, its controller adds an "
     "additional {G} for each Elf on the battlefield.", Kind.ENCHANTMENT),
    ("Whenever you tap a land creature for mana, add an additional {G}.", Kind.CREATURE),
    ("Enchant land\nEnchanted land has \"{T}: Add two mana of any one color.\"",
     Kind.ENCHANTMENT),
    ("Creatures you control have \"{T}: Add {C}. This mana can't be spent to cast a "
     "nonartifact spell.\"", Kind.ARTIFACT),
    ("Whenever a player taps a land for mana, that player adds one mana of any type that land "
     "produced, and this enchantment deals 1 damage to the player.", Kind.ENCHANTMENT),
    ("Add {R} for each creature you control.", Kind.SORCERY),
])
def test_what_the_engine_does_not_play_is_not_read(text, kind):
    assert rule_of(text, kind) is None


# --- the engine -----------------------------------------------------------------

FOREST = Card("Forest", 0, 0, 0, LAND, subtypes=frozenset({"forest"}), basic=True)
ISLAND = Card("Island", 0, 0, 0, LAND, subtypes=frozenset({"island"}), basic=True)
WASTES = Card("Wastes", 0, 0, 0, LAND, mana_abilities=(ManaAbility(FLAT, {"C": 1}),),
              basic=True)
TOWER = Card("Command Tower", 0, 0, 0, LAND, mana_abilities=(ManaAbility(FLAT, {"UG": 1}),))
SOL_RING = Card("Sol Ring", 1, 0, 1, ROCK, mana_abilities=(ManaAbility(FLAT, {"C": 2}),))
SIGNET = Card("Simic Signet", 2, 0, 2, ROCK,
              mana_abilities=(ManaAbility(FLAT, {"G": 1, "U": 1}, activation_generic=1),))
BEAR = Card("Bear", 2, 0, 1, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}),
            colors=frozenset({"G"}))
ELF = Card("Llanowar Elves", 1, 0, 0, CREATURE, cost=parse("{G}"),
           types=frozenset({"creature"}), mana_abilities=(ManaAbility(FLAT, {"G": 1}),))
PRIEST = Card("Big Dork", 2, 0, 1, CREATURE, types=frozenset({"creature"}),
              mana_abilities=(ManaAbility(FLAT, {"G": 2}),))


def permanent(name, rule, subtype="", produces=(), kind=ENCHANTMENT, enchants="", **extra):
    types = {ENCHANTMENT: {"enchantment"}, CREATURE: {"creature"}}.get(kind, {"artifact"})
    return Card(name, 1, 0, 1, kind, types=frozenset(types), enchants=enchants,
                mana_abilities=(ManaAbility(rule, produces, subtype=subtype, **extra),))


WILD_GROWTH = permanent("Wild Growth", EXTRA, "enchanted", {"G": 1}, enchants="land")
SPRAWL = permanent("Utopia Sprawl", EXTRA, "enchanted:forest", color="chosen",
                   enchants="forest")
WAKE = permanent("Mirari's Wake", EXTRA, "land")
KINNAN = permanent("Kinnan", EXTRA, "nonland", kind=CREATURE)
MONUMENT = permanent("Forsaken Monument", EXTRA, "colorless", {"C": 1}, kind=ROCK)
BADGERMOLE = permanent("Badgermole Cub", EXTRA, "creature", {"G": 1}, kind=CREATURE)
CAGED_SUN = permanent("Caged Sun", EXTRA, "chosen_land", color="chosen", kind=ROCK)
REFLECTION = permanent("Mana Reflection", MULTIPLY, "permanent", times=2)
CRYPTOLITH = permanent("Cryptolith Rite", GRANT, "creature", {"WUBRG": 1})
ABUNDANT = permanent("Abundant Growth", GRANT, "enchanted", {"WUBRG": 1}, enchants="land")


def board(lands=(), rocks=(), creatures=(), others=(), deck=None):
    game = Game(random.Random(1), deck=deck)
    game.lands, game.rocks = list(lands), list(rocks)
    game.creatures, game.other_permanents = list(creatures), list(others)
    return game


def made(game):
    return game.mana().by_color()


def test_wild_growth_adds_a_green_to_its_land():
    assert made(board([FOREST, ISLAND], others=[WILD_GROWTH])) == {"G": 2, "U": 1}
    assert made(board(others=[WILD_GROWTH])) == {}, "no land, nothing to enchant"


def test_wild_growth_is_not_added_again_for_a_land_entering_mid_turn():
    game = board([FOREST], others=[WILD_GROWTH])
    actions.OpenMainPhase().run(game, None)
    assert game.pool.by_color() == {"G": 2}
    game._enter_land(ISLAND, tapped=False)
    assert game.pool.by_color() == {"G": 2, "U": 1}


def test_an_aura_needs_its_land_to_be_cast():
    sprawl = Card("Utopia Sprawl", 1, 0, 0, ENCHANTMENT, cost=parse("{G}"),
                  types=frozenset({"enchantment"}), enchants="forest",
                  mana_abilities=SPRAWL.mana_abilities)
    game = board([ISLAND], rocks=[SOL_RING])
    actions.OpenMainPhase().run(game, None)
    game.pool.add("G", 1)
    assert not game.can_cast(sprawl, game.pool)
    game.lands.append(FOREST)
    assert game.can_cast(sprawl, game.pool)


def test_utopia_sprawl_adds_its_chosen_colour_only_with_a_forest():
    game = board([ISLAND], others=[SPRAWL])
    game.chosen_colors = {"Utopia Sprawl": "U"}
    assert made(game) == {"U": 1}
    game.lands.append(FOREST)
    assert made(game) == {"U": 2, "G": 1}


def deck_of(*costs):
    library = tuple(Card(f"Spell {i}", 2, 0, 0, CREATURE, cost=parse(cost))
                    for i, cost in enumerate(costs))
    return DeckDefinition(name="Test", commander=None, library=library)


def test_a_colour_is_chosen_once_and_by_what_the_bonus_is_for():
    game = board([FOREST, FOREST, ISLAND], others=[SPRAWL],
                 deck=deck_of("{G}", "{U}", "{B}"))
    # Caged Sun pays off on the colour most lands make; Utopia Sprawl fixes
    # the one fewest make - black, which no land here does.
    assert game.chosen_color(CAGED_SUN, CAGED_SUN.mana_abilities[0]) == "G"
    assert game.chosen_color(SPRAWL, SPRAWL.mana_abilities[0]) == "B"
    game.lands = [ISLAND] * 5
    assert game.chosen_color(CAGED_SUN, CAGED_SUN.mana_abilities[0]) == "G", "chosen once"


def test_mirari_s_wake_adds_one_of_what_each_land_made():
    # The Tower made a choice: its bonus is {C}, or one land would pay {G}{U}.
    assert made(board([FOREST, TOWER], others=[WAKE])) == {"G": 2, "UG": 1, "C": 1}


def test_kinnan_adds_to_nonland_sources_only():
    game = board([FOREST, FOREST], rocks=[SOL_RING, SIGNET], creatures=[KINNAN])
    # Sol Ring {C}{C} + {C}; the Signet takes one of those for {G}{U} + one of them.
    assert made(game) == {"C": 2, "G": 3, "U": 1, "UG": 1}


def test_forsaken_monument_adds_to_colourless_mana():
    assert made(board([FOREST], rocks=[SOL_RING, MONUMENT])) == {"G": 1, "C": 3}


def test_badgermole_cub_adds_to_creatures_tapped_for_mana():
    assert made(board(creatures=[ELF, BADGERMOLE])) == {"G": 2}


def test_caged_sun_makes_a_choice_land_tap_for_its_colour():
    game = board([FOREST, TOWER, ISLAND], rocks=[CAGED_SUN])
    game.chosen_colors = {"Caged Sun": "G"}
    assert made(game) == {"G": 4, "U": 1}


def test_mana_reflection_doubles_what_a_source_makes():
    game = board([FOREST, TOWER], rocks=[SOL_RING], others=[REFLECTION])
    assert made(game) == {"G": 2, "UG": 1, "C": 5}


def test_cryptolith_rite_lets_each_creature_tap_for_any_colour():
    game = board(creatures=[BEAR, ELF, PRIEST], others=[CRYPTOLITH])
    # The Elf's own {G} is no more than any colour; the big dork keeps its two.
    assert made(game) == {"WUBRG": 2, "G": 2}


def test_a_creature_cast_this_turn_does_not_tap_for_mana():
    game = board([FOREST, FOREST], others=[CRYPTOLITH])
    game.hand = [BEAR]
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert game.pool.total == 0
    game.pool = None
    actions.OpenMainPhase().run(game, None)
    assert game.pool.by_color() == {"G": 2, "WUBRG": 1}


def test_abundant_growth_turns_a_colourless_land_first():
    assert made(board([FOREST, WASTES], others=[ABUNDANT])) == {"G": 1, "WUBRG": 1}
    assert made(board([FOREST], others=[ABUNDANT])) == {"WUBRG": 1}


def test_bloom_tender_makes_each_colour_among_your_permanents():
    tender = Card("Bloom Tender", 2, 0, 1, CREATURE, types=frozenset({"creature"}),
                  colors=frozenset({"G"}),
                  mana_abilities=(ManaAbility(COUNTS, subtype="colors_among"),))
    game = board([FOREST], creatures=[tender])
    assert dict(game.mana_ability(tender).produces) == {"G": 1}
    game.creatures.append(Card("Gold", 2, 0, 0, CREATURE, colors=frozenset({"W", "U"})))
    assert dict(game.mana_ability(tender).produces) == {"W": 1, "U": 1, "G": 1}


def test_sanctum_weaver_counts_the_enchantments_itself_included():
    weaver = Card("Sanctum Weaver", 2, 0, 1, CREATURE,
                  types=frozenset({"creature", "enchantment"}), colors=frozenset({"G"}),
                  mana_abilities=(ManaAbility(COUNTS, subtype="enchantment"),))
    game = board([FOREST], creatures=[weaver], others=[WILD_GROWTH],
                 deck=deck_of("{G}", "{W}"))
    game.hand = [Card("White Spell", 2, 0, 0, ENCHANTMENT, cost=parse("{W}{W}"))]
    # All of it in one colour: the one the hand asks for.
    assert dict(game.mana_ability(weaver).produces) == {"W": 2}


def test_overgrown_battlement_counts_creatures_with_defender():
    wall = Card("Overgrown Battlement", 2, 0, 1, CREATURE, defender=True,
                mana_abilities=(ManaAbility(COUNTS, subtype="creature:defender", color="G"),))
    game = board(creatures=[wall, BEAR, Card("Wall", 1, 0, 1, CREATURE, defender=True)])
    assert dict(game.mana_ability(wall).produces) == {"G": 2}


def test_battle_hymn_makes_one_red_for_each_creature():
    hymn = Card("Battle Hymn", 2, 0, 1, RITUAL, ritual_color="R", ritual_counts="creature")
    assert board(creatures=[BEAR, ELF, PRIEST]).ritual_mana(hymn) == ("R", 3)


def test_high_tide_counts_the_islands_in_the_pool_less_the_one_that_paid():
    tide = Card("High Tide", 1, 0, 0, RITUAL, cost=parse("{U}"), ritual_color="U",
                ritual_counts="tapped:island")
    game = board([ISLAND, ISLAND, ISLAND, FOREST])
    game.hand = [tide]
    actions.OpenMainPhase().run(game, None)
    assert game.ritual_mana(tide) == ("U", 2)
    actions.CastSpell(index=0).run(game, None)
    assert game.pool.by_color() == {"U": 4, "G": 1}


# --- the card page ---------------------------------------------------------------


def test_the_card_page_says_what_comes_on_top():
    assert _ability_text(WILD_GROWTH.mana_abilities[0]) == (
        "1 G more when the enchanted land is tapped for mana")
    assert _ability_text(REFLECTION.mana_abilities[0]) == (
        "a permanent tapped for mana makes 2 times as much")
    assert _ability_text(CRYPTOLITH.mana_abilities[0]) == "your creatures tap for 1 W/U/B/R/G"
