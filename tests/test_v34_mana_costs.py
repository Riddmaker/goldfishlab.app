"""Engine v34: mana abilities with a cost the engine did not pay.

1. **A planeswalker's "+1: Add {R}{R}."** (Chandra, Torch of Defiance) or
   "0:" (Ugin): the mana each turn, from the turn after it arrives, and never
   its other abilities - its loyalty never falls. Not a minus, not mana only
   some spells may spend (Jaya Ballard), not a back face (Arlinn at night),
   not an amount that scales (Xenagos).
2. **Several creatures tapped at once**: Heritage Druid's three Elves for
   {G}{G}{G}, Supportive Parents' two creatures for one of any colour. Not
   The Cabbage Merchant's Foods.
3. **Sphere of the Suns** "enters tapped and with three charge counters".
"""

import random

import pytest

from cards.models import OracleCard
from cards.profiles import _mana_production
from simulation import build
from simulation.cards import CREATURE, PLANESWALKER, DeckDefinition, ManaAbility, TapMana
from simulation.game import Game
from simulation.manacost import parse
from simulation.rules.mana import FLAT
from simulations import editor
from tests.engine_builders import tap

ANY = "WUBRG"


def card(text, type_line, name="Test Card"):
    return OracleCard(name=name, front_name=name, oracle_text=text, type_line=type_line,
                      produced_mana=list(ANY))


WALKER = "Legendary Planeswalker — Chandra"

# --- the reader -----------------------------------------------------------------------


@pytest.mark.parametrize("text, amount", [
    ("+1: Exile the top card of your library. You may cast that card.\n+1: Add {R}{R}.\n"
     "−3: Chandra deals 4 damage to target creature.", 2),
    ("+2: Add two mana in any combination of colors.\n−X: Chandra deals X damage.", 2),
    ("+2: You gain 3 life and draw a card.\n0: Add {C}{C}{C}.", 3),
    ("Creatures you control get +1/+0.\n+1: Add {R} or {G}. Creature spells you cast this turn "
     "can't be countered.", 1),
])
def test_a_planeswalkers_mana_for_loyalty_it_gains_is_read(text, amount):
    reading = _mana_production(card(text, WALKER))
    assert (reading.amount, reading.notes) == (amount, [])


@pytest.mark.parametrize("text, type_line, note", [
    ("−1: Add {R}{R}.", WALKER, "a mana ability that costs '−1' is not modelled"),
    ("+1: Add {R}{R}{R}. Spend this mana only to cast instant or sorcery spells.", WALKER,
     "its mana is restricted to certain spells or moments"),
    ("+1: Add X mana in any combination of {R} and/or {G}, where X is the number of creatures "
     "you control.", WALKER, "mana amount scales with the board"),
    ("+1: Untap target land.\n//\n+2: Add {R}{G}.", f"{WALKER} // {WALKER}",
     "a mana ability that costs '+2' is not modelled"),
    ("+1: Add {R}{R}.", "Legendary Creature — Human", "a mana ability that costs '+1' is not "
     "modelled"),
])
def test_other_loyalty_mana_stays_a_gap(text, type_line, note):
    reading = _mana_production(card(text, type_line))
    assert reading.amount is None and note in reading.notes


def test_heritage_druid_taps_three_elves():
    reading = _mana_production(card("Tap three untapped Elves you control: Add {G}{G}{G}.",
                                    "Creature — Elf Druid"))
    assert reading.tap_mana == {"types": ["creature"], "filter": "elf", "per": 3, "amount": 3,
                                "produces": {"G": 3}, "spend_only": None, "token": 0}


def test_supportive_parents_taps_two_creatures():
    reading = _mana_production(card("Tap two untapped creatures you control: Add one mana of "
                                    "any color.", "Creature — Human"))
    assert (reading.tap_mana["filter"], reading.tap_mana["per"]) == ("", 2)


def test_sphere_of_the_suns_enters_with_three_counters():
    reading = _mana_production(card(
        "This artifact enters tapped and with three charge counters on it.\n{T}, Remove a charge "
        "counter from this artifact: Add one mana of any color.", "Artifact",
        name="Sphere of the Suns"))
    assert (reading.store["start"], reading.store["use"], reading.notes) == (3, "tap", [])


# --- the engine -------------------------------------------------------------------------


def elf(name):
    return build.card(name, 1, 0, 1, CREATURE, cost=parse("{G}"), types=frozenset({"creature"}),
                      creature_types=frozenset({"elf"}))


DRUID = build.card("Heritage Druid", 1, 0, 1, CREATURE, cost=parse("{G}"),
                   types=frozenset({"creature"}), creature_types=frozenset({"elf"}),
                   tap_mana=TapMana(types=frozenset({"creature"}), filter="elf", mana="G",
                                    amount=3, per=3))
CHANDRA = build.card("Chandra, Torch of Defiance", 4, 0, 4, PLANESWALKER,
                     cost=parse("{2}{R}{R}"), types=frozenset({"planeswalker"}),
                     mana_abilities=(ManaAbility(FLAT, {"R": 2}),))


def game_with(creatures=(), other=()):
    game = Game(random.Random(1), deck=DeckDefinition("v34", None, (elf("Filler"),)))
    game.creatures, game.other_permanents = list(creatures), list(other)
    tap(game, lands=0, rocks=0)
    return game


def test_chandra_adds_her_mana_each_turn():
    assert game_with(other=[CHANDRA]).mana().by_color() == {"R": 2}


def test_heritage_druid_makes_three_for_each_three_elves():
    assert game_with([DRUID, elf("A"), elf("B")]).mana().by_color() == {"G": 3}
    assert game_with([DRUID, elf("A")]).mana().by_color() == {}, "two Elves are not three"


def test_a_fourth_elf_stays_untapped_to_attack():
    game = game_with([DRUID, elf("A"), elf("B"), elf("C")])
    tapped, _ = game.tap_fodder()
    taken = tapped[next(iter(tapped))]
    assert len(taken) == 3 and DRUID not in taken
    assert game.mana().by_color() == {"G": 3}


def test_the_card_page_says_how_many_at_once():
    assert editor._text("tap_mana", type("Shown", (), {"card": DRUID})) == (
        "tap 3 creature at once: 3 mana")
