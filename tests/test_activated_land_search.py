"""P19 R14 (engine version 18): land searches that are activated.

Wayfarer's Bauble, Burnished Hart, Myriad Landscape, Urza's Cave, the
Panoramas and Krosan Verge pay a cost and sacrifice themselves for their
lands; Lander tokens do the same for {2}. The agent activates them with mana
nothing else wants, before an X spell takes the rest, and only when it gains
a land or a colour. Knight of the White Orchid searches only while an opponent
has more lands - on the assumption that each plays one a turn - and a Saga's
first chapter happens as it enters. A search in combat stays a gap with its
own reason.

Also here: the report's "two mana sources that grow with the board" no longer
counts a filter land or Reflecting Pool, which make a fixed amount.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _land_search, _landers, derive
from simulation import actions, agent
from simulation.analysis import _scaling_mana_sources
from simulation.cards import (
    ARTIFACT,
    COUNTS,
    CREATURE,
    FILTER,
    FLAT,
    LAND,
    LANDS_COULD_PRODUCE,
    PER_CONTROLLED,
    SORCERY,
    Card,
    DeckDefinition,
    LandSearch,
    ManaAbility,
)
from simulation.game import Game
from simulation.manacost import parse

Kind = DerivedProfile.Kind

# --- the reader -----------------------------------------------------------------


def read(text, kind=Kind.ARTIFACT):
    return _land_search(OracleCard(oracle_text=text, type_line="Artifact"), kind).spec


def test_wayfarers_bauble_is_read_whole():
    spec = read("{2}, {T}, Sacrifice this artifact: Search your library for a basic land card, "
                "put that card onto the battlefield tapped, then shuffle.")
    assert (spec["when"], spec["cost"], spec["taps"], spec["sacrifice"]) == (
        "activate", "{2}", True, True)
    assert (spec["battlefield"], spec["basic"], spec["tapped"]) == (1, True, True)


def test_burnished_hart_needs_no_tap():
    spec = read("{3}, Sacrifice this creature: Search your library for up to two basic land "
                "cards, put them onto the battlefield tapped, then shuffle.", Kind.CREATURE)
    assert (spec["cost"], spec["taps"], spec["battlefield"]) == ("{3}", False, 2)


def test_myriad_landscape_finds_two_of_one_type():
    spec = read("{2}, {T}, Sacrifice this land: Search your library for up to two basic land "
                "cards that share a land type, put them onto the battlefield tapped, then "
                "shuffle.", Kind.LAND)
    assert spec["share_type"] and spec["battlefield"] == 2


def test_krosan_verge_finds_one_of_each():
    spec = read("{2}, {T}, Sacrifice this land: Search your library for a Forest card and a "
                "Plains card, put them onto the battlefield tapped, then shuffle.", Kind.LAND)
    assert spec["each"] and spec["types"] == ["forest", "plains"] and spec["battlefield"] == 2
    assert spec["basic"] is False


def test_a_coloured_activation_cost_is_kept():
    spec = read("{3}{G}, {T}, Sacrifice this land: Search your library for up to two basic land "
                "cards, put them onto the battlefield tapped, then shuffle.", Kind.LAND)
    assert spec["cost"] == "{3}{G}"


def test_sacrificing_this_land_must_be_a_land():
    assert read("{2}, {T}, Sacrifice this land: Search your library for a basic land card, "
                "put it onto the battlefield tapped, then shuffle.", Kind.ARTIFACT) is None


@pytest.mark.parametrize("text", [
    "When this creature enters, if an opponent controls more lands than you, you may search "
    "your library for a Plains card, put it onto the battlefield, then shuffle.",
    "Keen Sight — When this creature enters, if an opponent controls more lands than you, "
    "search your library for a basic Plains card, put it onto the battlefield tapped, then "
    "shuffle.",
])
def test_an_opponent_with_more_lands_is_a_condition(text):
    spec = read(text, Kind.CREATURE)
    assert (spec["when"], spec["condition"]) == ("enters", "opponent_more_lands")


def test_a_sagas_first_chapter_happens_as_it_enters():
    spec = read("(As this Saga enters and after your draw step, add a lore counter.)\n"
                "I — Crescent Fang — Search your library for a basic land card, put it onto "
                "the battlefield tapped, then shuffle.\nII — Something else.", Kind.ENCHANTMENT)
    assert spec["when"] == "enters"


def test_a_search_in_combat_says_so():
    found = _land_search(OracleCard(oracle_text=(
        "Whenever equipped creature attacks, you may search your library for a basic land "
        "card, put it onto the battlefield tapped, then shuffle."), type_line="Artifact"),
        Kind.ARTIFACT)
    assert found.spec is None and "combat" in found.reason


LANDER = ('(It\'s an artifact with "{2}, {T}, Sacrifice this token: Search your library for a '
          'basic land card, put it onto the battlefield tapped, then shuffle.")')


@pytest.mark.parametrize("text, kind, count", [
    (f"When this creature enters, create a Lander token. {LANDER}", Kind.CREATURE, 1),
    (f"You gain 2 life. Create a Lander token. {LANDER}", Kind.SORCERY, 1),
    # Made only sometimes, or for someone else, or sacrificed again later.
    (f"Counter target spell unless its controller pays {{2}}. If they do, you create a "
     f"Lander token. {LANDER}", Kind.INSTANT, 0),
    (f"Destroy target nonland permanent. Its controller creates a Lander token. {LANDER}",
     Kind.INSTANT, 0),
    (f"When this creature enters, create a Lander token. At the beginning of the end step on "
     f"your next turn, sacrifice that token. {LANDER}", Kind.CREATURE, 0),
    (f"Whenever you attack a player, create a Lander token. {LANDER}", Kind.CREATURE, 0),
])
def test_lander_tokens_are_read_like_treasure(text, kind, count):
    assert _landers(OracleCard(oracle_text=text, type_line="Creature"), kind) == count


@pytest.mark.django_db
@pytest.mark.parametrize("text, type_line, gap", [
    (f"When this creature enters, create a Lander token. {LANDER}", "Creature — Human", False),
    (f"Whenever you attack a player, create a Lander token. {LANDER}", "Creature — Insect",
     True),
])
def test_the_landers_search_is_not_the_cards_own(text, type_line, gap):
    card = OracleCard(name="Lander Maker", front_name="Lander Maker", type_line=type_line,
                      oracle_text=text, mana_cost="{2}{G}", cmc=3)
    profile = derive(card, set())
    assert not profile.tutor_to
    assert profile.landers == (0 if gap else 1)
    assert any("Lander" in reason for reason in profile.review_reasons) == gap


# --- the engine -----------------------------------------------------------------


def land(name, subtype="", *, basic=True, search=None, colorless=False):
    abilities = (ManaAbility(FLAT, {"C": 1}),) if colorless else ()
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset({subtype}) if subtype else frozenset(),
                basic=basic, land_search=search, mana_abilities=abilities)


FOREST, ISLAND, SWAMP = land("Forest", "forest"), land("Island", "island"), land("Swamp", "swamp")
PLAINS = land("Plains", "plains")
BAUBLE = Card("Wayfarer's Bauble", 1, 0, 1, ARTIFACT, cost=parse("{1}"), land_search=LandSearch(
    when="activate", cost=parse("{2}"), taps=True, sacrifice=True))
HART = Card("Burnished Hart", 3, 0, 3, CREATURE, cost=parse("{3}"), land_search=LandSearch(
    battlefield=2, when="activate", cost=parse("{3}"), sacrifice=True))
MYRIAD = land("Myriad Landscape", basic=False, colorless=True, search=LandSearch(
    battlefield=2, when="activate", cost=parse("{2}"), taps=True, sacrifice=True,
    share_type=True))
VERGE = land("Krosan Verge", basic=False, colorless=True, search=LandSearch(
    battlefield=2, basic=False, types=frozenset({"forest", "plains"}), each=True,
    when="activate", cost=parse("{2}"), taps=True, sacrifice=True))
CAVE = land("Urza's Cave", basic=False, colorless=True, search=LandSearch(
    basic=False, when="activate", cost=parse("{3}"), taps=True, sacrifice=True))


def game_with(library, *, lands=(), others=(), creatures=(), hand=()):
    game = Game(random.Random(1), deck=DeckDefinition("R14", None, (FOREST,)))
    game.library = list(library)
    game.lands = list(lands)
    game.other_permanents = list(others)
    game.creatures = list(creatures)
    game.hand = list(hand)
    game.tapped_lands = 0
    game.turn = 3
    actions.OpenMainPhase().run(game, None)
    return game


def test_the_bauble_pays_two_and_finds_a_basic_tapped():
    game = game_with([ISLAND], lands=[FOREST, FOREST], others=[BAUBLE])
    assert actions.ActivateSearch(zone=actions.OTHER, index=0) in actions.legal_actions(game)
    actions.ActivateSearch(zone=actions.OTHER, index=0).run(game, None)
    assert BAUBLE in game.graveyard and not game.other_permanents
    assert game.pool.total == 0
    assert game.lands[0] is ISLAND and game.tapped_lands == 1


def test_without_the_mana_it_cannot_be_activated():
    game = game_with([ISLAND], lands=[FOREST], others=[BAUBLE])
    assert not game.can_activate(BAUBLE, game.pool)
    with pytest.raises(actions.IllegalAction):
        actions.ActivateSearch(zone=actions.OTHER, index=0).run(game, None)


def test_a_land_that_taps_for_it_gives_up_its_own_mana():
    # Two Forests and Myriad Landscape made {G}{G}{C}: {2} and Myriad's own
    # {C} is all three.
    game = game_with([ISLAND, ISLAND, SWAMP], lands=[FOREST, FOREST, MYRIAD])
    assert game.pool.total == 3
    assert game.can_activate(MYRIAD, game.pool)
    game.activate(MYRIAD, game.pool)
    assert game.pool.total == 0
    assert MYRIAD in game.graveyard
    # Two that share a land type: the Islands, not an Island and the Swamp.
    assert sorted(card.name for card in game.lands[:2]) == ["Island", "Island"]


def test_a_land_that_entered_tapped_cannot_pay_its_tap():
    game = game_with([ISLAND], lands=[MYRIAD, FOREST, FOREST, FOREST])
    game.tapped_lands = 1
    assert not game.can_activate(MYRIAD, game.pool)


def test_krosan_verge_finds_a_forest_and_a_plains():
    game = game_with([FOREST, FOREST, PLAINS, ISLAND], lands=[SWAMP, SWAMP, VERGE])
    game.activate(VERGE, game.pool)
    assert sorted(card.name for card in game.lands[:2]) == ["Forest", "Plains"]


def test_a_lander_is_a_basic_land_for_two():
    game = game_with([ISLAND], lands=[FOREST, FOREST])
    assert not game.can_activate(None, game.pool)
    game.landers = 1
    assert actions.ActivateLander() in actions.legal_actions(game)
    actions.ActivateLander().run(game, None)
    assert game.landers == 0 and game.lands[0] is ISLAND


def test_a_lander_maker_makes_its_token():
    maker = Card("Galactic Wayfarer", 3, 0, 2, CREATURE, cost=parse("{2}{G}"), landers=1)
    game = game_with([], lands=[FOREST, FOREST, FOREST], hand=[maker])
    actions.CastSpell(index=0).run(game, None)
    assert game.landers == 1


def test_the_agent_activates_with_what_is_left_before_an_x_spell():
    blaze = Card("Blaze", 1, 0, 0, SORCERY, cost=parse("{X}{R}"), x_count=1)
    game = game_with([ISLAND], lands=[FOREST, FOREST, FOREST, land("Mountain", "mountain")],
                     others=[BAUBLE], hand=[blaze])
    agent._cast_best(game, game.pool)
    assert BAUBLE in game.graveyard and blaze in game.hand


def test_the_agent_trades_a_land_only_for_a_missing_colour():
    # Urza's Cave for a Forest the lands can already make: no.
    game = game_with([FOREST], lands=[FOREST, FOREST, FOREST, CAVE])
    assert not agent._try_activation(game, game.pool)
    # For an Island they cannot: yes.
    game = game_with([ISLAND], lands=[FOREST, FOREST, FOREST, CAVE])
    assert agent._try_activation(game, game.pool)
    assert ISLAND in game.lands and CAVE in game.graveyard


def test_hart_is_activated_for_two_lands():
    game = game_with([FOREST, FOREST], lands=[FOREST, FOREST, FOREST], creatures=[HART])
    assert agent._try_activation(game, game.pool)
    assert len(game.lands) == 5


def test_knight_of_the_white_orchid_searches_only_when_behind():
    knight = Card("Knight of the White Orchid", 2, 2, 0, CREATURE, cost=parse("{W}{W}"),
                  land_search=LandSearch(tapped=False, basic=False, types=frozenset({"plains"}),
                                         when="enters", condition="opponent_more_lands"))
    ahead = game_with([PLAINS], lands=[PLAINS, PLAINS], hand=[knight])
    actions.CastSpell(index=0).run(ahead, None)
    assert PLAINS in ahead.library
    behind = game_with([PLAINS], lands=[PLAINS, PLAINS], hand=[knight])
    behind.turn = 4
    actions.CastSpell(index=0).run(behind, None)
    assert PLAINS not in behind.library


def test_the_card_page_names_the_cost():
    from simulations.engine.adapter import _land_search_text

    assert _land_search_text(BAUBLE.land_search) == (
        "{2}, {T}, sacrifice it: 1 basic land onto the battlefield, tapped")
    assert _land_search_text(HART.land_search) == (
        "{3}, sacrifice it: 2 basic lands onto the battlefield, tapped")
    assert "of one land type" in _land_search_text(MYRIAD.land_search)


# --- the report's scaling sources -----------------------------------------------


def source(name, ability):
    return Card(name, 0, 0, 0, LAND, mana_abilities=(ability,))


def test_a_fixed_amount_does_not_grow_with_the_board():
    fixed = [
        source("Fetid Heath", ManaAbility(FILTER, {"WB": 2}, pays_with="WB")),
        source("Reflecting Pool", ManaAbility(LANDS_COULD_PRODUCE, {})),
        source("Urza's Tower", ManaAbility(COUNTS, {"C": 7}, subtype="names:Urza's Mine")),
    ]
    growing = [
        source("Cabal Coffers", ManaAbility(PER_CONTROLLED, {}, subtype="swamp",
                                            activation_generic=2)),
        source("Gaea's Cradle", ManaAbility(COUNTS, {}, subtype="creature", color="G")),
    ]
    assert _scaling_mana_sources(fixed) == 0
    assert _scaling_mana_sources(growing) == 2


# --- the playtest ---------------------------------------------------------------


def test_the_board_posts_an_activation():
    from playtest.forms import ActionForm
    from playtest.views import _tiles

    tiles = _tiles([FOREST, BAUBLE], {}, zone=actions.OTHER, activatable={1})
    assert [tile["activatable"] for tile in tiles] == [False, True]
    assert tiles[1]["zone"] == actions.OTHER
    form = ActionForm({"kind": "activate_search", "zone": actions.OTHER, "index": 1})
    assert form.is_valid(), form.errors
    assert form.action() == actions.ActivateSearch(zone=actions.OTHER, index=1)
    lander = ActionForm({"kind": "activate_lander"})
    assert lander.is_valid() and lander.action() == actions.ActivateLander()
