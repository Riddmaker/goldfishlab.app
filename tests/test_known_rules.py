"""P19 R4 (engine version 8): what the engine already knew, read off the card.

"Activate only if you control ..." checked in the game; Urborg, Crypt Ghast
and Cabal Coffers as the rules the engine has played since Phase 2; and the
cards that were never missing anything - a search that is somebody else's, a
land that really makes no mana.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _activation_condition, _mana_production, _mana_rule, _tutor
from simulation.cards import ARTIFACT, FLAT, LAND, ROCK, Card, ManaAbility, TappedUnless
from simulation.game import Game

Kind = DerivedProfile.Kind


def oracle(text: str, type_line="Land", name="Test Card") -> OracleCard:
    return OracleCard(oracle_text=text, type_line=type_line, name=name, front_name=name,
                      produced_mana=["B", "C", "G"])


# --- the reader: conditions -----------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("Activate only if you control five or more lands.", {"kind": "lands", "count": 5}),
    ("Activate only if you control an artifact.", {"kind": "artifacts", "count": 1}),
    ("Activate only if you control three or more artifacts.",
     {"kind": "artifacts", "count": 3}),
    ("Activate only if you control a Swamp.", {"kind": "control_type", "types": ["swamp"]}),
    ("Activate only if you control an Island or a Mountain.",
     {"kind": "control_type", "types": ["island", "mountain"]}),
])
def test_a_condition_is_read(text, expected):
    assert _activation_condition(text) == expected


def test_a_condition_the_board_cannot_answer_is_not_read():
    assert _activation_condition(
        "Activate only if you control a creature with power 4 or greater.") is None


def test_temple_of_the_false_god_makes_two_from_the_fifth_land():
    reading = _mana_production(oracle(
        "{T}: Add {C}{C}. Activate only if you control five or more lands."))
    assert (reading.amount, reading.produces) == (2, {"C": 2})
    assert reading.condition == {"if": {"kind": "lands", "count": 5}, "otherwise": None}
    assert not reading.notes


def test_a_tainted_land_falls_back_to_colourless():
    reading = _mana_production(oracle(
        "{T}: Add {C}.\n{T}: Add {B} or {G}. Activate only if you control a Swamp."))
    # With a Swamp: a choice of all its mana - which colours, `produced_mana` says.
    assert (reading.amount, reading.produces) == (1, None)
    assert reading.condition == {"if": {"kind": "control_type", "types": ["swamp"]},
                                 "otherwise": {"amount": 1, "produces": {"C": 1},
                                               "activation": 0}}


def test_spend_only_mana_is_read_with_its_condition():
    """Since engine version 21 the spells it pays for are read too (P19 R17)."""
    reading = _mana_production(oracle(
        "{T}: Add {C}.\n{T}: Add one mana of any color. Spend this mana only to cast a "
        "creature spell. Activate only if you control a Swamp."))
    assert reading.condition["if"] == {"kind": "control_type", "types": ["swamp"]}
    assert reading.spend_only["spells"][0]["types"] == ["creature"]
    assert not reading.notes


def test_spend_only_mana_for_what_the_engine_cannot_tell_stays_a_gap():
    reading = _mana_production(oracle(
        "{T}: Add {C}.\n{T}: Add one mana of any color. Spend this mana only to cast "
        "spells from your graveyard."))
    assert reading.spend_only is None
    assert any("restricted" in note for note in reading.notes)


# --- the reader: rules ----------------------------------------------------------


@pytest.mark.parametrize("text, kind, rule", [
    ("Each land is a Swamp in addition to its other land types.", Kind.LAND,
     {"rule": "type_adding", "subtype": "swamp", "activation": 0, "color": ""}),
    ("Extort\nWhenever you tap a Swamp for mana, add an additional {B}.", Kind.CREATURE,
     {"rule": "double_subtype", "subtype": "swamp", "activation": 0, "color": "B"}),
    ("Whenever a Forest is tapped for mana, its controller adds an additional {G}.",
     Kind.ENCHANTMENT,
     {"rule": "double_subtype", "subtype": "forest", "activation": 0, "color": "G"}),
    ("{2}, {T}: Add {B} for each Swamp you control.", Kind.LAND,
     {"rule": "per_controlled", "subtype": "swamp", "activation": 2, "color": "B"}),
])
def test_a_rule_the_engine_plays_is_read(text, kind, rule):
    assert _mana_rule(oracle(text), kind) == rule


def test_a_coffers_with_a_second_ability_is_not_coffers():
    """Cabal Stronghold also taps for {C} and counts basic Swamps only: not the
    land-subtype rule Coffers plays, but a count of its own (P19 R11)."""
    rule = _mana_rule(oracle("{T}: Add {C}.\n{3}, {T}: Add {B} for each basic Swamp you "
                             "control."), Kind.LAND)
    assert rule["rule"] == "counts" and rule["subtype"] == "basic:swamp"


# --- the reader: nothing missing ------------------------------------------------


def test_a_search_by_the_targets_controller_is_not_our_tutor():
    path = OracleCard(oracle_text="Exile target creature. Its controller may search their "
                                  "library for a basic land card, put that card onto the "
                                  "battlefield tapped, then shuffle.")
    found = _tutor(path, {"tutor"})
    assert (found.zone, found.reason) == ("", "")


# --- the engine -----------------------------------------------------------------


def land(name, subtypes=(), abilities=(), types=frozenset({"land"})):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset(subtypes),
                mana_abilities=tuple(abilities), types=types)


SWAMP = land("Swamp", {"swamp"})
TEMPLE = land("Temple of the False God", abilities=[
    ManaAbility(FLAT, {"C": 2}, only_if=TappedUnless("lands", count=5))])
TAINTED = land("Tainted Wood", abilities=[
    ManaAbility(FLAT, {"BG": 1}, only_if=TappedUnless("control_type",
                                                      types=frozenset({"swamp"}))),
    ManaAbility(FLAT, {"C": 1})])
OPAL = Card("Mox Opal", 0, 0, 0, ROCK, types=frozenset({ARTIFACT}), mana_abilities=(
    ManaAbility(FLAT, {"WUBRG": 1}, only_if=TappedUnless("artifacts", count=3)),))
TRINKET = Card("Trinket", 1, 0, 1, ARTIFACT, types=frozenset({ARTIFACT}))


def game_with(lands=(), rocks=(), others=()):
    game = Game(random.Random(1))
    game.lands, game.rocks, game.other_permanents = list(lands), list(rocks), list(others)
    game.tapped_lands = game.tapped_rocks = 0
    return game


def test_temple_taps_for_nothing_before_the_fifth_land():
    assert game_with([TEMPLE, SWAMP, SWAMP, SWAMP]).mana().total == 3
    assert game_with([TEMPLE, SWAMP, SWAMP, SWAMP, SWAMP]).mana().total == 6


def test_a_tainted_land_needs_its_land_type():
    alone = game_with([TAINTED, land("Island", {"island"})]).mana()
    assert alone.amount("C") == 1 and alone.amount("BG") == 0
    beside = game_with([TAINTED, SWAMP]).mana()
    assert beside.amount("BG") == 1 and beside.amount("C") == 0


def test_mox_opal_counts_itself_among_three_artifacts():
    assert game_with(rocks=[OPAL], others=[TRINKET]).mana().total == 0
    assert game_with(rocks=[OPAL], others=[TRINKET, TRINKET]).mana().total == 1


def test_the_agent_holds_temple_back_until_it_makes_mana():
    from simulation.agent import _land_score

    early = game_with([SWAMP, SWAMP])
    late = game_with([SWAMP, SWAMP, SWAMP, SWAMP])
    assert _land_score(TEMPLE, early) < _land_score(TEMPLE, late)


def test_the_card_page_says_when_the_mana_comes():
    from simulations.engine.adapter import _ability_text

    assert _ability_text(TEMPLE.mana_abilities[0]) == "2 C if you control 5 or more lands"
    assert _ability_text(TAINTED.mana_abilities[0]) == (
        "1 B/G if you control a land of type Swamp")
