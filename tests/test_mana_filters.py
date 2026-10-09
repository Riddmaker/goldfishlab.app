"""P19 R6 (engine version 10): filter lands and converters.

A filter (Fetid Heath: ``{W/B}, {T}: Add {W}{W}, {W}{B}, or {B}{B}``) turns its
own {C} and one white or black mana into two that are each white or black -
done when the pool opens, and only when nothing is narrowed by it. A converter
(Study Hall: ``{1}, {T}: Add one mana of any color``) costs a mana to fix a
colour, so the payment decides: only when it would fail otherwise.
"""

import random

import pytest

from cards.models import OracleCard
from cards.profiles import _mana_production
from simulation.cards import FILTER, FLAT, LAND, Card, ManaAbility
from simulation.game import Game
from simulation.mana import ManaPool
from simulation.manacost import parse


def production(text: str):
    return _mana_production(OracleCard(oracle_text=text, type_line="Land", name="Test",
                                       front_name="Test", produced_mana=["B", "C", "W"]))


# --- the reader -----------------------------------------------------------------


def test_a_filter_land_taps_for_colourless_and_filters_beside_it():
    reading = production("{T}: Add {C}.\n{W/B}, {T}: Add {W}{W}, {W}{B}, or {B}{B}.")
    assert (reading.amount, reading.produces, reading.notes) == (1, {"C": 1}, [])
    assert reading.filter == {"pays_with": "WB", "amount": 2, "offers": "WB",
                              "produces": None}


def test_a_converter_is_read_beside_its_colourless():
    reading = production("{T}: Add {C}.\n{1}, {T}: Add one mana of any color.")
    assert (reading.amount, reading.notes) == (1, [])
    assert reading.filter == {"pays_with": "", "amount": 1, "offers": "", "produces": None}


def test_a_filter_with_a_cost_it_cannot_pay_stays_a_gap():
    reading = production("{T}: Add {G}.\n{2}{G}{G}, {T}: Add six {G}.")
    assert reading.filter is None
    assert any("filter" in note for note in reading.notes)


# --- the engine: filters --------------------------------------------------------


def land(name, subtypes=(), abilities=()):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset(subtypes),
                mana_abilities=tuple(abilities), types=frozenset({"land"}))


HEATH = land("Fetid Heath", abilities=[ManaAbility(FLAT, {"C": 1}),
                                       ManaAbility(FILTER, {"WB": 2}, pays_with="WB")])
STUDY = land("Study Hall", abilities=[ManaAbility(FLAT, {"C": 1}),
                                      ManaAbility(FILTER, {"WUB": 1})])
PLAINS, ISLAND = land("Plains", {"plains"}), land("Island", {"island"})
TOWER = land("Command Tower", abilities=[ManaAbility(FLAT, {"WUB": 1})])


def game_with(lands):
    game = Game(random.Random(1))
    game.lands = list(lands)
    game.tapped_lands = 0
    return game


def test_a_filter_turns_a_plains_and_its_colourless_into_two_of_its_colours():
    pool = game_with([HEATH, PLAINS]).mana()
    assert pool.amount("WB") == 2 and pool.total == 2
    assert pool.can_pay_cost(parse("{B}{B}"))


def test_a_filter_never_takes_a_wider_choice():
    """A Command Tower's W/U/B is worth more than one of two W/B."""
    pool = game_with([HEATH, TOWER]).mana()
    assert pool.amount("WUB") == 1 and pool.amount("C") == 1


def test_a_filter_with_nothing_to_filter_taps_for_colourless():
    pool = game_with([HEATH, ISLAND]).mana()
    assert pool.amount("C") == 1 and pool.amount("U") == 1


# --- the engine: converters -----------------------------------------------------


def test_a_converter_waits_in_the_pool_as_colourless():
    pool = game_with([STUDY, ISLAND, ISLAND]).mana()
    assert pool.amount("C") == 1 and pool.converters == ["WUB"]
    assert pool.reach().get("B") == 1, "the colour report knows it could make black"


def test_a_converter_is_used_only_when_a_colour_is_missing():
    pool = game_with([STUDY, ISLAND, ISLAND]).mana()
    # {2}: paid with the {C} and an Island, the converter not touched.
    plain = pool.copy()
    assert plain.pay_cost(parse("{2}")) is not None and plain.converters == ["WUB"]
    # {B}: the {C} and an Island become one black mana - one mana is gone.
    payment = pool.pay_cost(parse("{B}"))
    assert payment is not None
    assert pool.total == 1 and pool.amount("U") == 1 and pool.converters == []


def test_a_converter_costs_a_mana_so_it_cannot_pay_what_needs_all_of_them():
    pool = game_with([STUDY, ISLAND, ISLAND]).mana()
    assert not pool.can_pay_cost(parse("{2}{B}"))
    assert pool.can_pay_cost(parse("{1}{B}"))


@pytest.mark.parametrize("cost", ["{B}{B}", "{W}{U}{B}"])
def test_two_converters_do_not_conjure_more_than_they_have(cost):
    pool = ManaPool(C=2, U=1)
    pool.converters = ["WUB", "WUB"]
    assert pool.can_pay_cost(parse("{B}")) and not pool.can_pay_cost(parse(cost))
