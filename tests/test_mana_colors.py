"""Mana in five colours: making it, and paying with it.

`tests/test_manacost.py` covers parsing a cost and paying it exactly. This file
covers the other half - that the engine can now *produce* something other than
black, and that a card can carry a cost that is not mono-black.

The tests worth reading are the ones that would have passed for the wrong
reason before: a blue pool that was silently black, a Forest under Yavimaya
that made colourless mana, and a green Medallion that made black spells
cheaper.
"""

import random

import pytest

from simulation.agent import _land_score
from simulation.cards import (
    DOUBLE_SUBTYPE,
    FLAT,
    FOREST_SUBTYPE,
    LAND,
    PER_CONTROLLED,
    RITUAL,
    SORCERY,
    SWAMP_SUBTYPE,
    TYPE_ADDING,
    Card,
    CostReduction,
    DeckDefinition,
    ManaAbility,
)
from simulation.game import Game
from simulation.mana import (
    ManaPool,
    available_mana,
    doublers,
    effective_mana_cost,
    land_color,
    reductions_from,
)
from simulation.manacost import ManaCost, parse

# --- the pieces a deck is built from ----------------------------------------

FOREST = Card("Forest", 0, 0, 0, LAND, subtypes=frozenset({FOREST_SUBTYPE}))
PLAINS = Card("Plains", 0, 0, 0, LAND, subtypes=frozenset({"plains"}))
SWAMP = Card("Swamp", 0, 0, 0, LAND, subtypes=frozenset({SWAMP_SUBTYPE}))

#: A land with no subtype at all, so only its own ability speaks for it.
TOWER = Card("Tower", 0, 0, 0, LAND, mana_abilities=(ManaAbility(FLAT, {"C": 1}),))

#: Urborg's green twin: every land is also a Forest.
YAVIMAYA = Card("Yavimaya", 0, 0, 0, LAND,
                mana_abilities=(ManaAbility(TYPE_ADDING, subtype=FOREST_SUBTYPE),))

#: Crypt Ghast for Forests.
GREEN_GHAST = Card("Green Ghast", 4, 0, 4, "creature",
                   mana_abilities=(ManaAbility(DOUBLE_SUBTYPE, subtype=FOREST_SUBTYPE),))

BLACK_GHAST = Card("Crypt Ghast", 4, 1, 3, "creature",
                   mana_abilities=(ManaAbility(DOUBLE_SUBTYPE, subtype=SWAMP_SUBTYPE),))


# --- production: the ability ------------------------------------------------


def test_an_ability_can_make_any_colour():
    ability = ManaAbility(FLAT, {"green": 1})
    assert ability.produces == (("G", 1),)
    assert ability.total == 1
    assert ability.colored_total == 1
    assert ability.black == 0


def test_the_two_spellings_are_the_same_ability():
    """A fixture writes `black`, a database row writes `B`.

    They have to compare equal, or the same deck read from two places stops
    being the same deck.
    """
    assert ManaAbility(FLAT, {"black": 1}) == ManaAbility(FLAT, {"B": 1})


def test_zero_has_one_representation():
    assert ManaAbility(FLAT, {"B": 0}).produces == ()
    assert ManaAbility(FLAT, {}) == ManaAbility(FLAT)


def test_an_unknown_colour_is_refused():
    """Better a loud failure here than mana that quietly never gets spent."""
    with pytest.raises(ValueError, match="unknown mana source"):
        ManaAbility(FLAT, {"purple": 1})


def test_colourless_is_not_a_colour():
    ability = ManaAbility(FLAT, {"C": 2})
    assert ability.total == 2
    assert ability.colored_total == 0


def test_the_scaling_colour_follows_the_subtype():
    """Cabal Coffers counts Swamps and makes black; the Forest one makes green."""
    assert ManaAbility(PER_CONTROLLED, subtype=SWAMP_SUBTYPE).scaling_color == "B"
    assert ManaAbility(PER_CONTROLLED, subtype=FOREST_SUBTYPE).scaling_color == "G"
    # Gaea's Cradle counts creatures, so nothing about a land type says green.
    assert ManaAbility(PER_CONTROLLED, color="G").scaling_color == "G"
    assert ManaAbility(PER_CONTROLLED).scaling_color == "C"


# --- production: the pool ---------------------------------------------------


def test_blue_mana_is_not_black_mana():
    """`"blue".upper()[0]` is `"B"`. The pool used to key on that."""
    pool = ManaPool(blue=1)
    assert pool.amount("U") == 1
    assert pool.black == 0


def test_a_forest_taps_for_green():
    pool = available_mana([FOREST], [FOREST], [], {})
    assert pool.amount("G") == 1
    assert pool.total == 1


def test_yavimaya_works_like_urborg():
    """The whole point of the subtype-to-colour map.

    A land with no subtype and a colourless ability becomes a Forest, and a
    Forest taps for green - the same rule that made Phyrexian Tower tap for
    black under Urborg, with the colour no longer hard-coded.
    """
    assert available_mana([TOWER], [TOWER], [], {}).amount("C") == 1

    pool = available_mana([TOWER, YAVIMAYA], [TOWER], [], {})
    assert pool.amount("G") == 1
    assert pool.amount("C") == 0


def test_a_doubler_only_counts_its_own_subtype():
    extra = doublers([GREEN_GHAST])
    assert available_mana([FOREST], [FOREST], [], extra).amount("G") == 2
    assert available_mana([SWAMP], [SWAMP], [], extra).amount("B") == 1


def test_the_doubler_makes_its_own_colour_not_the_land_s():
    """Crypt Ghast reads "whenever you tap a Swamp for mana, add {B}".

    A Plains that is also a Swamp taps for {W} - and still adds the {B}. The
    bonus belongs to the doubler, not to the land that triggered it.
    """
    dual = Card("Godless Shrine", 0, 0, 0, LAND,
                subtypes=frozenset({"plains", SWAMP_SUBTYPE}))
    pool = available_mana([dual], [dual], [], doublers([BLACK_GHAST]))
    # The land itself makes a choice of W or B (engine version 5); the
    # doubler's bonus is black whatever the land was spent as.
    assert (pool.amount("WB"), pool.amount("B")) == (1, 1)
    assert pool.reach() == {"W": 1, "B": 2}


def test_a_land_of_two_types_makes_a_choice():
    """A land that is two basic types makes either colour (engine version 5).

    Until then the pool could not hold a choice and the first colour in WUBRG
    order won - which made every shock land a mono-coloured land, silently.
    """
    assert land_color(FOREST, frozenset({SWAMP_SUBTYPE})) == "BG"


def test_a_green_coffers_scales_in_green():
    forests = [FOREST] * 4
    coffers = Card("Green Coffers", 0, 0, 0, LAND,
                   mana_abilities=(ManaAbility(PER_CONTROLLED, subtype=FOREST_SUBTYPE,
                                               activation_generic=2),))
    lands = [coffers, *forests]

    pool = available_mana(lands, lands, [], {})
    # Four Forests, two of them spent on the activation, four more from it.
    assert pool.amount("G") == 6
    assert pool.total == 6


def test_a_colourless_land_still_sorts_below_a_coloured_one():
    """The land-choice rule asked `flat.black == 0`, which read every green
    land as colourless and played it last."""
    green_rock_land = Card("Green Land", 0, 0, 0, LAND,
                           mana_abilities=(ManaAbility(FLAT, {"G": 1}),))
    game = Game(random.Random(0))
    assert _land_score(green_rock_land, game) > _land_score(TOWER, game)


# --- costs ------------------------------------------------------------------


def test_a_card_without_a_cost_still_has_one():
    """The bridge: most cards are still described by two integers."""
    assert Card("Spell", 2, 1, 1, SORCERY).mana_cost == ManaCost.mono(1, 1)


def test_a_two_colour_cost_needs_both_colours():
    spell = Card("Bant Spell", 2, 0, 0, SORCERY, cost=parse("{G}{W}"))
    assert ManaPool(green=1, white=1).can_pay_cost(spell.mana_cost)
    assert not ManaPool(colorless=2).can_pay_cost(spell.mana_cost)
    assert not ManaPool(green=2).can_pay_cost(spell.mana_cost)


def test_a_medallion_only_helps_its_own_colour():
    jet = Card("Jet Medallion", 2, 0, 2, "rock",
               cost_reduction=CostReduction(amount=1, color="B"))
    emerald = Card("Emerald Medallion", 2, 0, 2, "rock",
                   cost_reduction=CostReduction(amount=1, color="G"))

    black_spell = Card("Black Spell", 3, 1, 2, SORCERY, cost=parse("{2}{B}"))
    assert effective_mana_cost(black_spell, reductions_from([jet])).generic == 1
    assert effective_mana_cost(black_spell, reductions_from([emerald])).generic == 2


def test_medallions_of_both_colours_stack_on_a_gold_spell():
    """Two Medallions really do both apply. The rules say so, and a simulation
    that maximised instead of summing would undercount the deck."""
    jet = Card("Jet Medallion", 2, 0, 2, "rock",
               cost_reduction=CostReduction(amount=1, color="B"))
    emerald = Card("Emerald Medallion", 2, 0, 2, "rock",
                   cost_reduction=CostReduction(amount=1, color="G"))
    gold = Card("Gold Spell", 5, 0, 0, SORCERY, cost=parse("{3}{B}{G}"))

    assert effective_mana_cost(gold, reductions_from([jet, emerald])).generic == 1


def test_an_unconditional_reducer_helps_everything():
    helm = Card("Helm of Awakening", 2, 0, 2, "rock",
                cost_reduction=CostReduction(amount=1, requires_pip=False))
    green = Card("Green Spell", 3, 0, 0, SORCERY, cost=parse("{2}{G}"))
    assert effective_mana_cost(green, reductions_from([helm])).generic == 1


def test_a_reduction_never_eats_a_coloured_pip():
    jet = Card("Jet Medallion", 2, 0, 2, "rock",
               cost_reduction=CostReduction(amount=3, color="B"))
    necro = Card("Necropotence", 3, 3, 0, SORCERY, cost=parse("{B}{B}{B}"))
    assert effective_mana_cost(necro, reductions_from([jet])) == necro.mana_cost


# --- a game with two colours in it ------------------------------------------


def _two_colour_game(hand, lands):
    deck = DeckDefinition(
        name="Selesnya",
        commander=None,
        library=(FOREST, PLAINS, FOREST, PLAINS),
    )
    game = Game(random.Random(0), deck=deck)
    game.hand = list(hand)
    game.lands = list(lands)
    return game


def test_a_gold_spell_is_castable_off_the_right_two_lands():
    spell = Card("Gold Spell", 2, 0, 0, SORCERY, cost=parse("{G}{W}"))

    right = _two_colour_game([spell], [FOREST, PLAINS])
    assert right.can_cast(spell, right.mana())

    wrong = _two_colour_game([spell], [FOREST, FOREST])
    assert not wrong.can_cast(spell, wrong.mana())


def test_casting_spends_the_colours_the_cost_asked_for():
    spell = Card("Gold Spell", 2, 0, 0, SORCERY, cost=parse("{G}{W}"))
    game = _two_colour_game([spell], [FOREST, PLAINS, FOREST])
    pool = game.mana()

    game.cast(spell, pool)

    assert (pool.amount("G"), pool.amount("W")) == (1, 0)


def test_a_red_ritual_adds_red():
    """`ritual_gain` was black by definition; now the colour is the card's."""
    mountain = Card("Mountain", 0, 0, 0, LAND, subtypes=frozenset({"mountain"}))
    ritual = Card("Rite of Flame", 1, 0, 0, RITUAL, cost=parse("{R}"),
                  ritual_gain=2, ritual_color="R")
    game = _two_colour_game([ritual], [mountain])
    pool = game.mana()

    game.cast(ritual, pool)

    assert pool.amount("R") == 2
    assert pool.black == 0


# --- reporting the colours, not just the total -------------------------------
#
# The last piece of colour work. The engine has made and spent five colours
# since Phase 2, but it only ever *reported* black: `game.black_available` was
# a single number and the analysis counted it. A deck whose mana is the wrong
# colour looked identical to one whose mana is right.


def test_the_pool_reports_every_colour_it_holds():
    pool = ManaPool(black=2, colorless=1, green=3)

    assert pool.by_color() == {"B": 2, "C": 1, "G": 3}


def test_a_colour_the_pool_never_held_is_absent_rather_than_zero():
    """An empty entry would read as a measurement; there was none to make."""
    assert ManaPool(black=1).by_color() == {"B": 1}


def test_black_available_is_derived_and_cannot_drift():
    """It used to be an assignable field, which is how it could disagree."""
    game = _two_colour_game([], [SWAMP])
    game.mana_by_color = {"B": 3, "G": 1}

    assert game.black_available == 3
    with pytest.raises(AttributeError):
        game.black_available = 7


def test_a_run_counts_each_colour_separately():
    """The aggregation half: a two-colour deck reports two colours."""
    from simulation import analysis

    deck = DeckDefinition(
        name="Two colours",
        commander=None,
        library=tuple([FOREST] * 20 + [SWAMP] * 20),
    )
    result = analysis.run(30, turns=3, seed=5, deck=deck)
    third = result["turn_stats"][2]

    assert third["mana_G"].total > 0
    assert third["mana_B"].total > 0
    # Nothing in the deck makes white, and the histogram says so rather than
    # being absent - every game contributed a zero.
    assert third["mana_W"].total == 0
    assert len(third["mana_W"]) == 30


def test_the_old_black_field_still_agrees_with_the_new_one():
    """`black` and `mana_B` are the same measurement, by design.

    `black` is kept because the golden parity snapshot is keyed on it. If the
    two ever disagreed, one of them would be lying and no test would say which.
    """
    from simulation import analysis

    deck = DeckDefinition(
        name="Mono black",
        commander=None,
        library=tuple([SWAMP] * 30 + [PLAINS] * 10),
    )
    result = analysis.run(25, turns=3, seed=11, deck=deck)

    for stats in result["turn_stats"]:
        assert stats["black"] == stats["mana_B"]
