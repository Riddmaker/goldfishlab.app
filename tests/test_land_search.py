"""P19 R2 (engine version 6): searches that put lands onto the battlefield.

The reader first - what it reads whole and what it leaves a gap - then the
engine: ramp spells, permanents that fetch on arrival, fetch lands.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _land_search
from simulation import actions
from simulation.cards import CREATURE, FLAT, LAND, SORCERY, Card, LandSearch, ManaAbility
from simulation.game import Game, best_land
from simulation.manacost import parse

Kind = DerivedProfile.Kind


def read(text: str, kind=Kind.SORCERY, type_line="Sorcery"):
    return _land_search(OracleCard(oracle_text=text, type_line=type_line), kind)


# --- the reader -----------------------------------------------------------------


def test_a_ramp_spell_is_read_whole():
    found = read("Search your library for a basic land card, put that card onto the "
                 "battlefield tapped, then shuffle.")
    assert found.spec == {"battlefield": 1, "hand": 0, "tapped": True, "basic": True,
                          "types": [], "life": 0, "when": "cast", "sacrifice": False,
                          "untap_at": 0, "cost": "", "taps": False, "share_type": False,
                          "each": False, "condition": ""}


def test_cultivate_puts_one_onto_the_battlefield_and_one_in_hand():
    found = read("Search your library for up to two basic land cards, reveal those cards, "
                 "put one onto the battlefield tapped and the other into your hand, "
                 "then shuffle.")
    assert (found.spec["battlefield"], found.spec["hand"], found.spec["tapped"]) == (1, 1, True)


def test_land_types_restrict_the_search_and_need_not_be_basic():
    found = read("Search your library for a Plains, Island, Swamp, or Mountain card, "
                 "put it onto the battlefield tapped, then shuffle.")
    assert found.spec["types"] == ["island", "mountain", "plains", "swamp"]
    assert found.spec["basic"] is False


def test_a_fetch_land_is_played_for_its_land_and_its_life():
    found = read("{T}, Pay 1 life, Sacrifice this land: Search your library for an Island "
                 "or Swamp card, put it onto the battlefield, then shuffle.",
                 Kind.LAND, "Land")
    spec = found.spec
    assert (spec["when"], spec["sacrifice"], spec["life"], spec["tapped"]) == (
        "play", True, 1, False)
    assert spec["types"] == ["island", "swamp"]


def test_fabled_passage_untaps_from_four_lands():
    found = read("{T}, Sacrifice this land: Search your library for a basic land card, put "
                 "it onto the battlefield tapped, then shuffle. Then if you control four "
                 "or more lands, untap that land.", Kind.LAND, "Land")
    assert found.spec["untap_at"] == 4


def test_a_creature_fetches_when_it_enters_or_when_sacrificed():
    elves = read("When this creature enters, search your library for a Forest card, put "
                 "that card onto the battlefield, then shuffle.", Kind.CREATURE)
    elder = read("Sacrifice this creature: Search your library for a basic land card, put "
                 "that card onto the battlefield tapped, then shuffle.", Kind.CREATURE)
    assert (elves.spec["when"], elves.spec["sacrifice"]) == ("enters", False)
    assert (elder.spec["when"], elder.spec["sacrifice"]) == ("enters", True)


def test_a_land_sacrificed_as_it_enters_is_a_fetch_land():
    found = read("When this land enters, sacrifice it. When you do, search your library for "
                 "a basic Forest, Plains, or Island card, put it onto the battlefield tapped, "
                 "then shuffle and you gain 1 life.", Kind.LAND, "Land")
    assert (found.spec["when"], found.spec["sacrifice"]) == ("play", True)
    assert found.spec["types"] == ["forest", "island", "plains"] and found.spec["basic"]


def test_an_enchantment_fetches_when_it_enters():
    found = read("When this enchantment enters, search your library for up to two basic "
                 "land cards, put them onto the battlefield tapped, then shuffle.",
                 Kind.ENCHANTMENT)
    assert (found.spec["when"], found.spec["battlefield"]) == ("enters", 2)


@pytest.mark.parametrize("text, kind, reason", [
    # An opponent's creature dying: a goldfish has no opponent. (Their lands
    # are an assumption since P19 R14 - Knight of the White Orchid.)
    ("Whenever a creature an opponent controls dies, you may search your library for a "
     "Plains card, put it onto the battlefield, then shuffle.", Kind.CREATURE, "condition"),
    # A creature's {T}: when it arrived is not tracked (activated ones are
    # played since P19 R14 - Myriad Landscape).
    ("{1}{G}, {T}, Sacrifice this creature: Search your library for a basic land card, put "
     "it onto the battlefield tapped, then shuffle.", Kind.CREATURE, "cost"),
    # Combat (P19 R14): the engine plays none.
    ("Whenever equipped creature attacks, you may search your library for a basic land "
     "card, put it onto the battlefield tapped, then shuffle.", Kind.ARTIFACT, "combat"),
    # A snow land: nothing here describes one.
    ("Search your library for a snow land card, put it onto the battlefield tapped, then "
     "shuffle.", Kind.SORCERY, "describe"),
])
def test_what_it_cannot_read_stays_a_gap(text, kind, reason):
    found = read(text, kind)
    assert found.spec is None
    assert reason in found.reason


def test_a_search_to_hand_is_not_a_land_search():
    assert read("Search your library for a basic land card, reveal it, put it into your "
                "hand, then shuffle.") == _land_search(OracleCard(oracle_text=""), Kind.SORCERY)


# --- the engine -----------------------------------------------------------------


def land(name, subtype="", *, basic=True, tapped=False, search=None):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset({subtype}) if subtype else frozenset(),
                basic=basic, enters_tapped=tapped, land_search=search)


FOREST, ISLAND, SWAMP = land("Forest", "forest"), land("Island", "island"), land("Swamp", "swamp")
SHOCK = land("Breeding Pool", "forest", basic=False)


def game_with(library, *, hand=(), lands=(), life=40):
    game = Game(random.Random(1))
    game.library = list(library)
    game.hand = list(hand)
    game.lands = list(lands)
    game.tapped_lands = 0
    game.life = life
    return game


def spell(name, search, kind=SORCERY, cost="{1}{G}"):
    return Card(name, 2, 0, 1, kind, cost=parse(cost), land_search=search)


def cast(game, card):
    game.hand.append(card)
    if game.pool is None:
        actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=game.hand.index(card)).run(game, None)


def test_rampant_growth_puts_a_basic_onto_the_battlefield_tapped():
    game = game_with([FOREST, ISLAND, SHOCK], lands=[FOREST, FOREST])
    cast(game, spell("Rampant Growth", LandSearch()))
    assert len(game.lands) == 3 and game.tapped_lands >= 1
    assert game.lands[0].basic
    assert SHOCK in game.library and len(game.library) == 2
    assert game.land_drop_used is False


def test_cultivate_puts_the_second_land_in_hand():
    game = game_with([FOREST, ISLAND, SWAMP], lands=[FOREST, FOREST, FOREST])
    cast(game, spell("Cultivate", LandSearch(battlefield=1, hand=1), cost="{2}{G}"))
    assert len(game.lands) == 4
    assert sum(1 for card in game.hand if card.is_land) == 1


def test_an_untapped_land_found_in_the_main_phase_pays_this_turn():
    game = game_with([FOREST], lands=[FOREST, FOREST])
    cast(game, spell("Nature's Lore", LandSearch(tapped=False, basic=False,
                                                 types=frozenset({"forest"}))))
    # Two Forests paid {1}{G}; the third is in the pool at once.
    assert game.pool.total == 1


def test_a_fetch_land_is_sacrificed_for_an_untapped_land_and_life():
    delta = land("Polluted Delta", basic=False, search=LandSearch(
        tapped=False, basic=False, types=frozenset({"island", "swamp"}), life=1,
        when="play", sacrifice=True))
    game = game_with([FOREST, ISLAND], hand=[delta])
    actions.PlayLand(index=0).run(game, None)
    assert delta in game.graveyard and delta not in game.lands
    assert game.lands == [ISLAND] and game.tapped_lands == 0
    assert game.life == 39
    assert game.land_drop_used


def test_fabled_passage_comes_in_untapped_from_the_fourth_land():
    passage = land("Fabled Passage", basic=False, search=LandSearch(
        when="play", sacrifice=True, untap_at=4))
    early = game_with([FOREST], hand=[passage], lands=[SWAMP])
    actions.PlayLand(index=0).run(early, None)
    assert early.tapped_lands == 1
    late = game_with([FOREST], hand=[passage], lands=[SWAMP, SWAMP, SWAMP])
    actions.PlayLand(index=0).run(late, None)
    assert late.tapped_lands == 0


def test_sakura_tribe_elder_is_sacrificed_for_its_land():
    elder = Card("Sakura-Tribe Elder", 2, 0, 1, CREATURE, cost=parse("{1}{G}"),
                 land_search=LandSearch(when="enters", sacrifice=True))
    game = game_with([FOREST], lands=[FOREST, FOREST])
    cast(game, elder)
    assert elder in game.graveyard and elder not in game.creatures
    assert len(game.lands) == 3


def test_the_search_takes_the_land_with_a_new_colour():
    game = game_with([], lands=[FOREST, FOREST])
    assert best_land(game, [FOREST, ISLAND]) is ISLAND
    tri = Card("Tri-land", 0, 0, 0, LAND, enters_tapped=True,
               mana_abilities=(ManaAbility(FLAT, {"UBR": 1}),))
    assert best_land(game, [ISLAND, tri]) is tri


def test_nothing_left_to_find_finds_nothing():
    game = game_with([], lands=[FOREST, FOREST])
    cast(game, spell("Rampant Growth", LandSearch()))
    assert len(game.lands) == 2


def test_the_card_page_says_what_the_search_does():
    from simulations.engine.adapter import _land_search_text

    assert _land_search_text(LandSearch(battlefield=1, hand=1)) == (
        "1 basic land onto the battlefield, tapped, and 1 to hand")
    assert _land_search_text(LandSearch(tapped=False, basic=False, life=1,
                                        types=frozenset({"swamp", "island"}))) == (
        "1 land (Island / Swamp) onto the battlefield, paying 1 life")
