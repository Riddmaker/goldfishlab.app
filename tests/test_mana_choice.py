"""P19 R1 (engine version 5): one mana of a choice of colours, settled when spent."""

import pytest

from simulation.cards import LAND, Card
from simulation.mana import ManaPool, available_mana, land_color
from simulation.manacost import (
    choice,
    is_choice,
    mana_label,
    normalised,
    parse,
    plan_payment,
)


def pay(pool: dict, cost: str):
    return plan_payment(pool, parse(cost))


def test_a_choice_is_written_in_wubrg_order_and_one_colour_is_no_choice():
    assert choice("GU") == "UG"
    assert choice(["R", "W", "B"]) == "WBR"
    assert choice("G") == "G"
    assert is_choice("UG") and not is_choice("G") and not is_choice("C")
    assert mana_label("UG") == "U/G" and mana_label("G") == "G"


def test_normalised_accepts_a_choice_in_any_order():
    assert normalised({"ru": 1, "B": 2}) == (("B", 2), ("UR", 1))
    with pytest.raises(ValueError):
        normalised({"UC": 1})


def test_a_choice_pays_either_of_its_colours():
    assert pay({"UR": 1}, "{U}").spent == {"UR": 1}
    assert pay({"UR": 1}, "{R}").spent == {"UR": 1}
    assert pay({"UR": 1}, "{G}") is None


def test_two_choices_are_matched_not_spent_greedily():
    """{U}{R} from a U/G and a U/R land: U must come from the U/G one."""
    assert pay({"UG": 1, "UR": 1}, "{U}{R}").spent == {"UG": 1, "UR": 1}
    assert pay({"UR": 2}, "{U}{G}") is None


def test_a_pip_takes_its_own_colour_before_a_choice():
    assert pay({"U": 1, "UR": 1}, "{U}").spent == {"U": 1}


def test_generic_keeps_the_choice_for_last():
    """{1}{R} from a Island and a U/R land: R from the choice, {1} from the Island."""
    assert pay({"U": 1, "UR": 1}, "{1}{R}").spent == {"U": 1, "UR": 1}
    assert pay({"UR": 1, "C": 1}, "{1}").spent == {"C": 1}


def test_hybrid_and_phyrexian_symbols_can_use_a_choice():
    assert pay({"WB": 1}, "{W/U}").spent == {"WB": 1}
    # One mana for {1} and nothing spare: the Phyrexian {G} is paid with life.
    payment = pay({"BG": 1}, "{1}{G/P}")
    assert (payment.spent, payment.life) == ({"BG": 1}, 2)
    payment = pay({"BG": 2}, "{1}{G/P}")
    assert (payment.spent, payment.life) == ({"BG": 2}, 0)


def test_a_choice_spent_on_a_hybrid_is_not_also_spent_on_a_pip():
    """One search, not two: {R}{R/G} from R/G and G must give R to the pip."""
    assert pay({"RG": 1, "G": 1}, "{R}{R/G}").spent == {"RG": 1, "G": 1}


def test_a_land_of_two_basic_types_makes_a_choice():
    crypt = Card("Blood Crypt", 0, 0, 0, LAND, subtypes=frozenset({"swamp", "mountain"}))
    assert land_color(crypt, frozenset()) == "BR"
    pool = available_mana([crypt], [crypt], [], {})
    assert pool.amount("BR") == 1 and pool.total == 1


def test_reach_counts_a_choice_toward_each_colour():
    pool = ManaPool(U=1, UR=2, C=1)
    assert pool.reach() == {"U": 3, "R": 2, "C": 1}
    assert pool.total == 4


def test_paying_from_a_pool_takes_the_choice_out():
    pool = ManaPool(UR=1, G=1)
    assert pool.pay_cost(parse("{R}{G}")) is not None
    assert pool.total == 0
