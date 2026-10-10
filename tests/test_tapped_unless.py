"""P19 R3 (engine version 7): lands that enter tapped unless something holds."""

import random

import pytest

from cards.models import OracleCard
from cards.profiles import _enters_tapped
from simulation import actions
from simulation.cards import LAND, Card, LandSearch, TappedUnless
from simulation.game import Game


def read(text: str):
    return _enters_tapped(OracleCard(name="Test Land", front_name="Test Land", oracle_text=text))


@pytest.mark.parametrize("text, condition", [
    ("This land enters tapped unless you control a Mountain or a Plains.",
     {"kind": "control_type", "types": ["mountain", "plains"]}),
    ("This land enters tapped unless you control two or fewer other lands.",
     {"kind": "lands", "count": 2, "at_least": False, "other": True, "basic": False, "type": ""}),
    ("This land enters tapped unless you control two or more basic lands.",
     {"kind": "lands", "count": 2, "at_least": True, "other": False, "basic": True, "type": ""}),
    ("This land enters tapped unless you control three or more other Islands.",
     {"kind": "lands", "count": 3, "at_least": True, "other": True, "basic": False,
      "type": "island"}),
    ("This land enters tapped unless you have two or more opponents.",
     {"kind": "opponents", "count": 2}),
    ("As this land enters, you may reveal a Plains or Swamp card from your hand. If you "
     "don't, this land enters tapped.", {"kind": "reveal", "types": ["plains", "swamp"]}),
    ("As this land enters, you may pay 2 life. If you don't, it enters tapped.",
     {"kind": "pay_life", "life": 2}),
    # P19 R12
    ("Minas Tirith enters tapped unless you control a legendary creature.",
     {"kind": "permanent", "types": ["creature"], "legendary": True}),
    ("This land enters tapped unless you control a planeswalker.",
     {"kind": "permanent", "types": ["planeswalker"], "legendary": False}),
    ("This land enters tapped unless you control a basic land.",
     {"kind": "lands", "count": 1, "basic": True}),
    ("This land enters tapped unless it's your first, second, or third turn of the game.",
     {"kind": "turn", "count": 3}),
    ("This land enters tapped unless your opponents control eight or more lands.",
     {"kind": "opponent_lands", "count": 8}),
    ("This land enters tapped unless a player has 13 or less life.",
     {"kind": "life_at_most", "count": 13}),
])
def test_the_condition_is_read_and_is_no_longer_a_gap(text, condition):
    assert read(text) == (True, "", condition)


def test_a_condition_it_cannot_read_stays_a_gap():
    tapped, note, condition = read(
        "This land enters tapped unless you control a legendary green creature.")
    assert tapped and "conditionally" in note and condition is None


def land(name, subtype="", *, basic=False, unless=None):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset({subtype}) if subtype else frozenset(),
                basic=basic, enters_tapped=unless is not None, tapped_unless=unless)


FOREST, ISLAND = land("Forest", "forest", basic=True), land("Island", "island", basic=True)


def play(land_card, *, lands=(), hand=(), life=40):
    game = Game(random.Random(1))
    game.library, game.hand, game.lands = [], [land_card, *hand], list(lands)
    game.tapped_lands, game.life = 0, life
    actions.PlayLand(index=0).run(game, None)
    return game


def test_a_check_land_is_untapped_with_its_type_out():
    check = land("Hinterland Harbor", unless=TappedUnless("control_type",
                                                          frozenset({"forest", "island"})))
    assert play(check, lands=[FOREST]).tapped_lands == 0
    assert play(check, lands=[]).tapped_lands == 1


def test_fast_and_slow_lands_count_the_other_lands():
    fast = land("Botanical Sanctum", unless=TappedUnless("lands", count=2, at_least=False,
                                                         other=True))
    slow = land("Dreamroot Cascade", unless=TappedUnless("lands", count=2, other=True))
    assert play(fast, lands=[FOREST, ISLAND]).tapped_lands == 0
    assert play(fast, lands=[FOREST, ISLAND, FOREST]).tapped_lands == 1
    assert play(slow, lands=[FOREST]).tapped_lands == 1
    assert play(slow, lands=[FOREST, ISLAND]).tapped_lands == 0


def test_a_commander_table_has_two_or_more_opponents():
    battlebond = land("Spectator Seating", unless=TappedUnless("opponents", count=2))
    assert play(battlebond).tapped_lands == 0


def test_a_snarl_reveals_a_card_from_hand():
    snarl = land("Vineglimmer Snarl", unless=TappedUnless("reveal",
                                                          frozenset({"forest", "island"})))
    assert play(snarl, hand=[FOREST]).tapped_lands == 0
    assert play(snarl, hand=[]).tapped_lands == 1


def test_a_shock_land_pays_two_life_but_not_below_the_floor():
    shock = land("Breeding Pool", "forest", unless=TappedUnless("pay_life", count=2))
    paid = play(shock)
    assert (paid.tapped_lands, paid.life) == (0, 38)
    low = play(shock, life=26)
    assert (low.tapped_lands, low.life) == (1, 26)


def test_a_shock_land_fetched_tapped_pays_nothing():
    shock = land("Breeding Pool", "forest", unless=TappedUnless("pay_life", count=2))
    game = Game(random.Random(1))
    game.library, game.lands, game.tapped_lands = [shock], [], 0
    game.search_lands(Card("Wilds", 0, 0, 0, LAND, land_search=LandSearch(basic=False)))
    assert (game.tapped_lands, game.life) == (1, 40)


# --- P19 R12 ------------------------------------------------------------------


def test_a_battle_land_does_not_count_itself_as_a_basic_land():
    battle = land("Prairie Stream", unless=TappedUnless("lands", count=2, basic=True))
    assert play(battle, lands=[FOREST]).tapped_lands == 1
    assert play(battle, lands=[FOREST, ISLAND]).tapped_lands == 0


def test_a_basic_land_out_lets_ba_sing_se_enter_untapped():
    temple = land("Ba Sing Se", unless=TappedUnless("lands", count=1, basic=True))
    assert play(temple, lands=[]).tapped_lands == 1
    assert play(temple, lands=[land("Bayou")]).tapped_lands == 1
    assert play(temple, lands=[FOREST]).tapped_lands == 0


def test_minas_tirith_needs_a_legendary_creature():
    tirith = land("Minas Tirith", unless=TappedUnless("permanent", frozenset({"creature"}),
                                                       legendary=True))
    plain = Card("Grizzly Bears", 2, 1, 1, "creature", types=frozenset({"creature"}))
    legend = Card("Tuvasa", 3, 3, 0, "creature", types=frozenset({"creature"}), legendary=True)
    game = Game(random.Random(1))
    game.library, game.hand, game.lands, game.tapped_lands = [], [tirith], [], 0
    game.creatures = [plain]
    assert not game.untaps_on_entering(tirith)
    game.creatures = [plain, legend]
    assert game.untaps_on_entering(tirith)


def test_starting_town_is_untapped_in_the_first_three_turns():
    town = land("Starting Town", unless=TappedUnless("turn", count=3))
    game = Game(random.Random(1))
    game.turn = 3
    assert game.untaps_on_entering(town)
    game.turn = 4
    assert not game.untaps_on_entering(town)


def test_a_turbulent_land_assumes_each_opponent_plays_a_land_a_turn():
    fen = land("Turbulent Fen", unless=TappedUnless("opponent_lands", count=8))
    game = Game(random.Random(1))
    game.turn = 3
    assert not game.untaps_on_entering(fen)
    game.turn = 4
    assert game.untaps_on_entering(fen)


def test_low_life_counts_only_your_own():
    campground = land("Abandoned Campground", unless=TappedUnless("life_at_most", count=13))
    game = Game(random.Random(1))
    assert not game.untaps_on_entering(campground)
    game.life = 13
    assert game.untaps_on_entering(campground)


def test_the_turbulent_assumption_is_a_stated_judgement_not_a_gap(db):
    import uuid

    from cards import profiles
    from simulations import gaps
    from simulations.engine import adapter

    fen = OracleCard.objects.create(
        oracle_id=uuid.uuid4(), name="Turbulent Fen", front_name="Turbulent Fen",
        search_name="turbulent fen", type_line="Land", produced_mana=["B", "G"],
        oracle_text="({T}: Add {B} or {G}.)\nThis land enters tapped unless your opponents "
                    "control eight or more lands.")
    profiles.rebuild(OracleCard.objects.filter(pk=fen.pk))
    card, found = adapter.engine_readings([fen], builtin=False)[fen.pk]
    assert card.tapped_unless == TappedUnless("opponent_lands", count=8)
    assumed = [gap for gap in found if gap.field == "assumed_lands"]
    assert assumed and gaps.kind_of("assumed_lands") == gaps.JUDGEMENT
    assert not gaps.of_kind(found, gaps.READING)
