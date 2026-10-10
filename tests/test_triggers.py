"""P19 R16 (engine version 20): triggers that make Treasure, mana or a card.

Landfall (Lotus Cobra, Tireless Provisioner), casting a spell (Birgi,
Storm-Kiln Artist, Lotho on the second), a creature dying (Pitiless
Plunderer, Pawn of Ulamog), a Dragon entering (Ganax), the upkeep and the
first main phase (Awakening Zone, Hulking Raptor), Brass's Bounty per land.
An Eldrazi Spawn is a Treasure that makes {C}. Smothering Tithe, Rhystic
Study and Esper Sentinel trigger on opponents who are not there: one a round,
on an assumption the deck page states beside the card. A Treasure or mana
made in combat stays unread, with its own reason.
"""

import random
import uuid

import pytest

from cards.models import OracleCard
from cards.profiles import _treasure_altar, _triggers, derive
from simulation import actions
from simulation.cards import (
    CREATURE,
    ENCHANTMENT,
    INSTANT,
    LAND,
    RITUAL,
    SORCERY,
    AdditionalCost,
    Card,
    DeckDefinition,
    LandSearch,
    Trigger,
)
from simulation.game import Game
from simulation.manacost import parse
from simulations import blindspots, deck_cards
from simulations.engine.adapter import Gap, Reading, _trigger_text
from simulations.engine.adapter import _triggers as adapter_triggers

# --- the reader -----------------------------------------------------------------


def read(text, name="Test Card"):
    return _triggers(OracleCard(name=name, front_name=name, oracle_text=text))


@pytest.mark.parametrize("text, expected", [
    ("Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you "
     "create a Treasure token. (It's an artifact with \"{T}, Sacrifice this token: Add one mana "
     "of any color.\")", {"event": "opponents", "treasures": 1, "tax": "{2}"}),
    ("Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
     {"event": "opponents", "draw": 1, "tax": "{1}"}),
    ("Whenever an opponent casts their first noncreature spell each turn, draw a card unless "
     "that player pays {X}, where X is this creature's power.",
     {"event": "opponents", "draw": 1, "tax": "{X}"}),
    ("Landfall — Whenever a land you control enters, create a Food token or a Treasure token.",
     {"event": "landfall", "filter": "", "treasures": 1}),
    ("Magecraft — Whenever you cast or copy an instant or sorcery spell, create a Treasure "
     "token.", {"event": "cast", "filter": "instant_sorcery", "treasures": 1}),
    ("Whenever another creature you control dies, create a Treasure token.",
     {"event": "dies", "filter": "another", "treasures": 1}),
    ("Whenever a player casts their second spell each turn, you lose 1 life and create a "
     "Treasure token.", {"event": "cast", "filter": "second", "treasures": 1, "life": 1}),
    ("Landfall — Whenever a land you control enters, add one mana of any color.",
     {"event": "landfall", "filter": "", "mana": 1, "color": "any"}),
    ("Whenever you cast a spell, add {R}. Until end of turn, you don't lose this mana as steps "
     "and phases end.", {"event": "cast", "filter": "", "mana": 1, "color": "R"}),
    ("Whenever you cast a colorless spell, create a 0/1 colorless Eldrazi Spawn creature token "
     "with \"Sacrifice this token: Add {C}.\"",
     {"event": "cast", "filter": "colorless", "treasures": 1, "spawn": True}),
    ("At the beginning of your upkeep, you may create a 0/1 colorless Eldrazi Spawn creature "
     "token. It has \"Sacrifice this token: Add {C}.\"",
     {"event": "upkeep", "filter": "", "treasures": 1, "spawn": True}),
    ("At the beginning of your first main phase, add {G}{G}.",
     {"event": "main", "filter": "", "mana": 2, "color": "G"}),
    ("For each land you control, create a Treasure token.",
     {"event": "resolves", "filter": "per_land", "treasures": 1}),
])
def test_a_trigger_is_read(text, expected):
    assert read(text).found == [expected]


def test_a_dragon_entering_counts_the_card_itself():
    found = read("Flying\nWhenever Ganax or another Dragon you control enters, create a "
                 "Treasure token.", name="Ganax, Astral Hunter")
    assert found.found == [{"event": "enters", "filter": "dragon", "treasures": 1}]
    # Somebody else's name is not "itself".
    assert read("Whenever Ganax or another Dragon you control enters, create a Treasure "
                "token.", name="Other Card").found == []


def test_pawn_of_ulamog_sees_its_own_death():
    found = read("Whenever this creature or another nontoken creature you control dies, you may "
                 "create a 0/1 colorless Eldrazi Spawn creature token. It has \"Sacrifice this "
                 "token: Add {C}.\"").found
    assert found == [{"event": "dies", "filter": "", "treasures": 1, "spawn": True}]


def test_upkeep_mana_counts_only_when_it_is_kept():
    # Gone by the main phase, unless the card says it is not.
    assert read("At the beginning of your upkeep, add {G}.").found == []
    assert read("At the beginning of your upkeep, add {G}. Until end of turn, you don't lose "
                "this mana as steps and phases end.").found == [
        {"event": "upkeep", "filter": "", "mana": 1, "color": "G"}]


@pytest.mark.parametrize("text", [
    "At the beginning of your end step, create a Treasure token for each creature that died "
    "this turn.",
    "Whenever a creature an opponent controls dies, create a Treasure token.",
    "Whenever you cast a spell, add {R}{G}.",
])
def test_what_it_cannot_play_is_not_read(text):
    assert read(text).found == []


def test_a_combat_treasure_is_its_own_reason():
    reading = read("Haste\nWhenever Captain Lannery Storm attacks, create a Treasure token.\n"
                   "Whenever you sacrifice a Treasure, Captain Lannery Storm gets +1/+0 until end "
                   "of turn.", name="Captain Lannery Storm")
    assert reading.combat and reading.explained and not reading.found


@pytest.mark.django_db
def test_smothering_tithe_is_read_whole():
    card = OracleCard(name="Smothering Tithe", front_name="Smothering Tithe",
                      type_line="Enchantment", mana_cost="{3}{W}", cmc=4,
                      produced_mana=["W", "U", "B", "R", "G"],
                      oracle_text=("Whenever an opponent draws a card, that player may pay {2}. "
                                   "If the player doesn't, you create a Treasure token. (It's an "
                                   "artifact with \"{T}, Sacrifice this token: Add one mana of "
                                   "any color.\")"))
    profile = derive(card, set())
    assert profile.triggers == [{"event": "opponents", "treasures": 1, "tax": "{2}"}]
    assert not profile.needs_review


@pytest.mark.django_db
def test_a_combat_treasure_on_a_card_read_before_stays_read():
    """Ragavan was read without his Treasure, which is pessimistic, not wrong:
    the combat reason only replaces a gap that was there."""
    card = OracleCard(name="Raider", front_name="Raider", type_line="Creature — Monkey",
                      mana_cost="{R}", cmc=1, produced_mana=[],
                      oracle_text=("Whenever Raider deals combat damage to a player, create a "
                                   "Treasure token."))
    assert not derive(card, set()).needs_review


def test_warren_soultrader_is_an_altar():
    found = _treasure_altar(OracleCard(oracle_text=(
        "Pay 1 life, Sacrifice another creature: Create a Treasure token. (It's an artifact with "
        "\"{T}, Sacrifice this token: Add one mana of any color.\")")))
    assert found == {"sacrifice": ["creature"], "filter": "", "amount": 1, "produces": None,
                     "taps": False, "life": 1, "other": True}


# --- the engine -----------------------------------------------------------------


def land(name, subtype):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset({subtype}), basic=True)


FOREST, ISLAND = land("Forest", "forest"), land("Island", "island")
COBRA = Card("Lotus Cobra", 2, 0, 1, CREATURE, cost=parse("{1}{G}"),
             triggers=(Trigger("landfall", mana=1, mana_color="G"),))
KILN = Card("Storm-Kiln Artist", 4, 0, 3, CREATURE, cost=parse("{3}{R}"),
            triggers=(Trigger("cast", filter="instant_sorcery", treasures=1,
                              treasure_mana="R"),))
BIRGI = Card("Birgi", 3, 0, 3, CREATURE, cost=parse("{3}"),
             triggers=(Trigger("cast", mana=1, mana_color="R"),))
LOTHO = Card("Lotho", 3, 0, 1, CREATURE, cost=parse("{1}{B}{R}"),
             triggers=(Trigger("cast", filter="second", treasures=1, treasure_mana="B", life=1),))
PLUNDERER = Card("Pitiless Plunderer", 4, 1, 3, CREATURE, cost=parse("{3}{B}"),
                 triggers=(Trigger("dies", filter="another", treasures=1, treasure_mana="B"),))
PAWN = Card("Pawn of Ulamog", 3, 2, 1, CREATURE, cost=parse("{1}{B}{B}"),
            triggers=(Trigger("dies", treasures=1, treasure_mana="C"),))
GANAX = Card("Ganax", 6, 0, 6, CREATURE, cost=parse("{6}"),
             creature_types=frozenset({"dragon"}),
             triggers=(Trigger("enters", filter="dragon", treasures=1, treasure_mana="R"),))
DRAGON = Card("Shivan Dragon", 6, 0, 6, CREATURE, cost=parse("{6}"),
              creature_types=frozenset({"dragon"}))
ZONE = Card("Awakening Zone", 3, 1, 2, ENCHANTMENT, cost=parse("{2}{G}"),
            triggers=(Trigger("upkeep", treasures=1, treasure_mana="C"),))
TITHE = Card("Smothering Tithe", 4, 0, 4, ENCHANTMENT, cost=parse("{4}"),
             triggers=(Trigger("opponents", treasures=1, treasure_mana="W"),))
STUDY = Card("Rhystic Study", 3, 0, 3, ENCHANTMENT, cost=parse("{3}"),
             triggers=(Trigger("opponents", draw=1),))
RAPTOR = Card("Hulking Raptor", 5, 2, 3, CREATURE, cost=parse("{2}{G}{G}{G}"),
              triggers=(Trigger("main", mana=2, mana_color="G"),))
BOUNTY = Card("Brass's Bounty", 7, 0, 7, SORCERY, cost=parse("{7}"),
              types=frozenset({"sorcery"}),
              triggers=(Trigger("resolves", filter="per_land", treasures=1, treasure_mana="R"),))
RITUAL_CARD = Card("Desperate Ritual", 2, 0, 1, RITUAL, cost=parse("{2}"), ritual_gain=3,
                   ritual_color="R", types=frozenset({"instant"}))
BEAR = Card("Grizzly Bears", 2, 0, 2, CREATURE, cost=parse("{1}{G}"))
RITES = Card("Village Rites", 1, 1, 0, INSTANT, cost=parse("{G}"), draw_on_cast=2,
             types=frozenset({"instant"}),
             additional_costs=(AdditionalCost(sacrifice=frozenset({"creature"})),))
GUIDE = Card("Simian Spirit Guide", 3, 0, 0, RITUAL, cost=parse(""), ritual_gain=1,
             ritual_color="R", exiled_on_cast=True)
SOULTRADER = Card("Warren Soultrader", 3, 1, 2, CREATURE, cost=parse("{2}{B}"),
                  sacrifice_mana=AdditionalCost(sacrifice=frozenset({"creature"}), life=1),
                  sacrifice_mana_amount=1, sacrifice_mana_color="B", sacrifice_mana_other=True)
FETCH = Card("Evolving Wilds", 0, 0, 0, LAND,
             land_search=LandSearch(when="play", tapped=True))


def game_with(library=(), *, lands=(), creatures=(), others=(), hand=(), open_main=True):
    game = Game(random.Random(1), deck=DeckDefinition("R16", None, (FOREST,)))
    game.library = list(library)
    game.lands = list(lands)
    game.creatures = list(creatures)
    game.other_permanents = list(others)
    game.hand = list(hand)
    game.tapped_lands = 0
    game.turn = 3
    if open_main:
        actions.OpenMainPhase().run(game, None)
    return game


def test_lotus_cobra_s_land_drop_waits_for_the_pool():
    game = game_with(lands=[FOREST], creatures=[COBRA], hand=[ISLAND], open_main=False)
    actions.PlayLand(index=0).run(game, None)
    assert game.pending_mana == ["G"]
    actions.OpenMainPhase().run(game, None)
    # Forest, Island and the Cobra's one.
    assert game.pool.total == 3 and game.mana_available == 3 and not game.pending_mana


def test_a_fetch_land_is_two_landfalls():
    game = game_with([FOREST], creatures=[COBRA], hand=[FETCH], open_main=False)
    actions.PlayLand(index=0).run(game, None)
    assert game.pending_mana == ["G", "G"]


def test_a_land_found_in_the_main_phase_adds_at_once():
    game = game_with(lands=[FOREST], creatures=[COBRA])
    game._enter_land(ISLAND, tapped=True)
    assert game.pool.total == 2


def test_storm_kiln_makes_a_treasure_for_an_instant_only():
    game = game_with(lands=[FOREST] * 3, creatures=[KILN], hand=[RITUAL_CARD, BEAR])
    actions.CastSpell(index=0).run(game, None)
    assert game.treasures == ["R"] and game.pool.treasures == ["R"]
    actions.CastSpell(index=0).run(game, None)
    assert game.treasures == ["R"]


def test_birgi_adds_after_each_spell_but_not_for_its_own():
    game = game_with(lands=[FOREST] * 3, hand=[BIRGI])
    actions.CastSpell(index=0).run(game, None)
    assert game.pool.total == 0
    game.hand = [BEAR]
    game.pool.add("G", 2)
    actions.CastSpell(index=0).run(game, None)
    assert game.pool.by_color() == {"R": 1}


def test_lotho_takes_the_second_spell_only():
    game = game_with(lands=[FOREST] * 4, creatures=[LOTHO], hand=[BEAR, BEAR, BEAR])
    for _ in range(2):
        actions.CastSpell(index=0).run(game, None)
    assert game.treasures == ["B"] and game.life == 39
    game.pool.add("G", 2)
    actions.CastSpell(index=0).run(game, None)
    assert game.treasures == ["B"]


def test_pitiless_plunderer_sees_another_die():
    game = game_with([ISLAND, ISLAND], lands=[FOREST], creatures=[PLUNDERER, BEAR], hand=[RITES])
    actions.CastSpell(index=0).run(game, None)
    assert BEAR in game.graveyard and game.treasures == ["B"]


def test_pitiless_plunderer_does_not_see_itself():
    game = game_with(lands=[FOREST], creatures=[PLUNDERER])
    game._sacrifice(PLUNDERER)
    assert game.treasures == []


def test_pawn_of_ulamog_leaves_a_spawn_behind():
    game = game_with(lands=[FOREST], creatures=[PAWN])
    game._sacrifice(PAWN)
    assert game.treasures == ["C"]


def test_a_sakura_tribe_elder_dies_too():
    elder = Card("Sakura-Tribe Elder", 2, 0, 1, CREATURE, cost=parse("{1}{G}"),
                 land_search=LandSearch(when="enters", sacrifice=True, tapped=True))
    game = game_with([FOREST], lands=[FOREST, FOREST], creatures=[PLUNDERER], hand=[elder])
    actions.CastSpell(index=0).run(game, None)
    assert elder in game.graveyard and game.treasures == ["B"]


def test_ganax_counts_itself_and_dragons():
    game = game_with(lands=[FOREST] * 12, hand=[GANAX, DRAGON, BEAR])
    actions.CastSpell(index=0).run(game, None)
    assert game.treasures == ["R"]
    actions.CastSpell(index=0).run(game, None)
    assert game.treasures == ["R", "R"]
    game.pool.add("G", 2)
    actions.CastSpell(index=0).run(game, None)
    assert len(game.treasures) == 2


def test_the_opponents_round_comes_before_your_next_turn():
    game = game_with([ISLAND] * 5, lands=[FOREST] * 4, hand=[TITHE, STUDY])
    actions.CastSpell(index=0).run(game, None)
    assert game.treasures == []
    game.pool.add("U", 3)
    actions.CastSpell(index=0).run(game, None)
    hand = len(game.hand)
    actions.BeginTurn().run(game, None)
    # One Treasure, and the turn's draw plus Rhystic Study's card.
    assert game.treasures == ["W"] and len(game.hand) == hand + 2


def test_awakening_zone_makes_a_spawn_each_upkeep():
    game = game_with([ISLAND] * 3, lands=[FOREST], others=[ZONE])
    actions.BeginTurn().run(game, None)
    actions.BeginTurn().run(game, None)
    assert game.treasures == ["C", "C"]


def test_hulking_raptor_adds_at_the_main_phase():
    game = game_with([ISLAND] * 3, lands=[FOREST], creatures=[RAPTOR], open_main=False)
    actions.OpenMainPhase().run(game, None)
    assert game.pool.by_color() == {"G": 3}


def test_brass_s_bounty_counts_the_lands():
    game = game_with(lands=[FOREST] * 7, hand=[BOUNTY])
    actions.CastSpell(index=0).run(game, None)
    assert game.treasures == ["R"] * 7


def test_a_spirit_guide_is_not_a_spell_cast():
    game = game_with(lands=[FOREST], creatures=[BIRGI], hand=[GUIDE])
    actions.CastSpell(index=0).run(game, None)
    assert game.pool.by_color() == {"G": 1, "R": 1} and game.spells_this_turn == 0


def test_warren_soultrader_pays_life_and_spares_itself():
    game = game_with(lands=[FOREST], creatures=[SOULTRADER])
    assert game.altar_fodder(SOULTRADER, game.pool) == []
    game = game_with(lands=[FOREST], creatures=[SOULTRADER, BEAR])
    assert game.altar_fodder(SOULTRADER, game.pool) == [BEAR]
    game.use_altar(SOULTRADER, game.pool)
    assert game.life == 39 and game.pool.by_color() == {"G": 1, "B": 1}


def test_a_trigger_mana_source_is_never_fodder():
    game = game_with(lands=[FOREST], creatures=[COBRA, BEAR])
    assert game.fodder(AdditionalCost(sacrifice=frozenset({"creature"}))) == [BEAR]


# --- the adapter and the deck page --------------------------------------------------


class Profile:
    def __init__(self, triggers):
        self.triggers = triggers


def test_a_spawn_makes_colourless_and_a_treasure_the_deck_s_colours():
    spawn, treasure = adapter_triggers(Profile([
        {"event": "upkeep", "treasures": 1, "spawn": True},
        {"event": "landfall", "treasures": 1}]), {}, frozenset({"G", "U"}))
    assert spawn.treasure_mana == "C" and treasure.treasure_mana == "UG"


def test_an_annotated_mana_switches_off_a_trigger_s_mana():
    assert adapter_triggers(Profile([{"event": "landfall", "mana": 1, "color": "any"}]),
                            {"mana_black": 1}, frozenset({"G"})) == ()


def test_the_card_page_says_what_a_trigger_does():
    assert _trigger_text(Trigger("cast", filter="instant_sorcery", treasures=1,
                                 treasure_mana="R")) == (
        "whenever you cast an instant or sorcery: 1 Treasure")
    assert _trigger_text(Trigger("enters", filter="dragon", treasures=1)) == (
        "whenever it or another Dragon of yours enters: 1 Treasure")
    assert _trigger_text(Trigger("upkeep", treasures=1, treasure_mana="C")) == (
        "each upkeep: 1 Eldrazi Spawn (one {C})")


def tithe_reading():
    template = ("makes a Treasure once a round, assuming one opponent a round does not pay "
                "%(tax)s")
    gap = Gap("Smothering Tithe", "assumed_trigger", template % {"tax": "{2}"}, template,
              {"tax": "{2}"})
    oracle = OracleCard(oracle_id=uuid.uuid4(), name="Smothering Tithe",
                        front_name="Smothering Tithe",
                        oracle_text="Whenever an opponent draws a card, that player may pay {2}.")
    return Reading(oracle_card=oracle, card=TITHE, gaps=[gap])


def test_the_card_list_states_the_assumption_beside_the_card():
    card = deck_cards.GridCard(reading=tithe_reading(), open=False, answered=False)
    assert card.assumptions == [
        "makes a Treasure once a round, assuming one opponent a round does not pay {2}"]


def test_the_panel_lists_it_as_an_assumption_not_as_dead():
    readings = [tithe_reading()]
    assert not blindspots.opponent_dependent(readings).suspects
    spot = blindspots.assumptions(readings)
    assert [(suspect.name, suspect.reason) for suspect in spot.suspects] == [
        ("Smothering Tithe",
         "makes a Treasure once a round, assuming one opponent a round does not pay {2}")]
