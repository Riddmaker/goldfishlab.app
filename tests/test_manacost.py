"""Costs in all five colours, and paying them exactly.

Parsing a cost and paying it exactly. `tests/test_mana_colors.py` covers the
other half: producing coloured mana, and cards that carry a coloured cost.

The tests that earn their keep are the hybrid ones. Hybrid payment is a
matching problem, and a greedy payer reports castable spells as uncastable -
which in a simulation quietly makes a deck look worse than it is.
"""

import pytest

from simulation.manacost import (
    PHYREXIAN_LIFE_FLOOR,
    Hybrid,
    ManaCost,
    parse,
    plan_payment,
)

# --- parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "colored", "generic", "mv"),
    [
        ("{3}{B}{B}", {"B": 2}, 3, 5),
        ("{B}", {"B": 1}, 0, 1),
        ("{1}", {}, 1, 1),
        ("", {}, 0, 0),
        ("{2}{W}{U}", {"W": 1, "U": 1}, 2, 4),
        ("{10}", {}, 10, 10),
    ],
)
def test_plain_costs_parse(text, colored, generic, mv):
    cost = parse(text)
    assert cost.colored == colored
    assert cost.generic == generic
    assert cost.mv == mv


def test_colorless_is_not_generic():
    """{C} can only be paid with colourless mana; {2} with anything."""
    cost = parse("{2}{C}")
    assert (cost.generic, cost.colorless) == (2, 1)
    assert cost.mv == 3


def test_hybrid_colors_are_kept_as_a_choice():
    cost = parse("{W/U}")
    assert cost.hybrid == (Hybrid(colors=("W", "U")),)
    assert cost.colored == {}, "a hybrid pip is not a fixed colour requirement"


def test_two_brid_records_both_ways_to_pay():
    """{2/B} is two generic OR one black."""
    cost = parse("{2/B}{2/B}")
    assert all(h.generic == 2 and h.colors == ("B",) for h in cost.hybrid)
    assert cost.mv == 2, "each symbol counts once toward mana value"


def test_phyrexian_records_its_colour():
    cost = parse("{B/P}")
    assert cost.phyrexian == ("B",)
    assert cost.mv == 1


def test_x_is_recorded_but_adds_nothing():
    cost = parse("{X}{B}")
    assert cost.has_x
    assert cost.mv == 1


def test_unmodelled_symbols_are_dropped_not_guessed():
    """Snow mana is not modelled. Better absent than silently wrong."""
    cost = parse("{S}{B}")
    assert cost.colored == {"B": 1}


# --- paying -----------------------------------------------------------------


def test_coloured_pips_need_that_colour():
    assert plan_payment({"B": 3}, parse("{B}{B}{B}")) is not None
    assert plan_payment({"C": 3}, parse("{B}{B}{B}")) is None


def test_generic_prefers_colourless():
    """Scarce coloured mana has to survive for the rest of the turn."""
    payment = plan_payment({"B": 2, "C": 2}, parse("{2}{B}"))
    assert payment is not None
    assert payment.spent["C"] == 2
    assert payment.spent["B"] == 1


def test_generic_falls_back_to_the_most_abundant_colour():
    payment = plan_payment({"B": 1, "G": 3}, parse("{2}{B}"))
    assert payment is not None
    assert payment.spent["G"] == 2
    assert payment.spent["B"] == 1


def test_colourless_requirement_cannot_be_paid_with_colour():
    assert plan_payment({"B": 5}, parse("{C}")) is None
    assert plan_payment({"C": 1}, parse("{C}")) is not None


# --- the hybrid cases a greedy payer gets wrong -----------------------------


def test_two_hybrids_must_take_different_colours():
    """The case that makes this a matching problem.

    One white and one blue pays {W/U}{W/U} only if the two symbols go to
    different colours. Pay the first greedily from white and the second is
    stranded - a greedy payer calls this uncastable.
    """
    assert plan_payment({"W": 1, "U": 1}, parse("{W/U}{W/U}")) is not None


def test_three_hybrids_over_two_colours_is_impossible():
    assert plan_payment({"W": 1, "U": 1}, parse("{W/U}{W/U}{W/U}")) is None


def test_a_forced_hybrid_is_resolved_before_a_free_one():
    """{W/U} with only blue available must take blue, leaving white for {W/B}.

    Resolving the symbol with fewer options first is what makes that happen.
    """
    assert plan_payment({"U": 1, "W": 1}, parse("{W/U}{W/B}")) is not None


def test_two_brid_falls_back_to_generic():
    """{2/B} with no black is payable with two of anything."""
    assert plan_payment({"G": 2}, parse("{2/B}")) is not None
    assert plan_payment({"G": 1}, parse("{2/B}")) is None


def test_two_brid_prefers_the_colour_when_it_is_cheaper():
    """One black beats two generic, and leaves more mana behind."""
    payment = plan_payment({"B": 1, "G": 2}, parse("{2/B}"))
    assert payment is not None
    assert payment.total == 1


# --- phyrexian --------------------------------------------------------------


def test_phyrexian_prefers_mana_over_life():
    payment = plan_payment({"B": 1}, parse("{B/P}"), life=40)
    assert payment is not None
    assert payment.life == 0


def test_phyrexian_pays_life_when_mana_is_short():
    payment = plan_payment({}, parse("{B/P}"), life=40)
    assert payment is not None
    assert payment.life == 2


def test_phyrexian_never_pays_below_the_life_floor():
    """Without an opponent, nothing punishes a low life total.

    An unbounded payer would treat life as free and overstate the deck.
    """
    assert plan_payment({}, parse("{B/P}"), life=PHYREXIAN_LIFE_FLOOR) is None
    assert plan_payment({}, parse("{B/P}"), life=PHYREXIAN_LIFE_FLOOR + 2) is not None


# --- the bridge from the old two-number cost --------------------------------


def test_mono_builds_the_old_shape():
    """`Card.pips` and `Card.generic` are still integers."""
    cost = ManaCost.mono(pips=2, generic=3)
    assert cost.colored == {"B": 2}
    assert cost.generic == 3
    assert cost.mv == 5


def test_mono_agrees_with_the_old_pool_rule():
    """The old rule: black >= pips and total >= pips + generic.

    Checked against the general payer for every small pool and cost, so the
    replacement cannot drift from what the 21 mana tests pin.
    """
    for black in range(5):
        for colorless in range(5):
            for pips in range(4):
                for generic in range(4):
                    old = black >= pips and (black + colorless) >= pips + generic
                    new = plan_payment(
                        {"B": black, "C": colorless}, ManaCost.mono(pips, generic)
                    ) is not None
                    assert old == new, (black, colorless, pips, generic)
