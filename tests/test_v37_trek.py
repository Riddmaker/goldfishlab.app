"""Engine v37: the Star Trek cards the engine could not read.

What these tests hold:

1. **Dal R'El**: "add {R} or {G}" as a land enters is one mana of either,
   the choice kept open until it is spent.
2. **Command Chair**: three mana of one colour, or as much as your
   commander's mana value while it is on the battlefield - whichever is more.
3. **Starfleet Academy**: {C}, or for {5} as much mana as the creature types
   among your creatures, the Borg left out.
4. **Captain Kirk, Boldly Going**: a basic land to hand as it enters and as
   it attacks; the named card and the graveyard are not searched.
5. **Quark, Humble Proprietor**: a Treasure in each upkeep - four a round,
   made in yours - and while it is out no Treasure is spent or sacrificed;
   they are kept, and come back once it is gone.
6. **Captain Jean-Luc Picard** stays unread, and says why: its search is an
   ability whose cost - seven Officers tapped - the engine does not pay.
"""

import random
import uuid

from cards.models import OracleCard
from cards.profiles import derive
from simulation import actions, build, serial
from simulation.cards import (
    COUNTS,
    CREATURE,
    FLAT,
    LAND,
    ROCK,
    AdditionalCost,
    Card,
    CombatTrigger,
    DeckDefinition,
    LandSearch,
    Trigger,
)
from simulation.game import TREASURE, Game
from simulation.manacost import parse
from simulation.spec import ManaAbility
from simulations.engine import adapter
from tests.engine_builders import tap
from tests.test_triggers import read


def oracle(text, name="Test Card", type_line="Artifact"):
    return OracleCard(name=name, front_name=name, oracle_text=text, type_line=type_line)


# --- the reader -----------------------------------------------------------------------


def test_dal_rel_adds_one_mana_of_either():
    found = read("Landfall — Whenever a land you control enters, add {R} or {G}.\n{3}, "
                 "Sacrifice a land: Draw a card.", name="Dal R'El, Augment Orphan").found
    assert found == [{"event": "landfall", "filter": "", "mana": 1, "color": "RG"}]


CHAIR_TEXT = ("This artifact enters tapped.\n{T}: Add three mana of any one color.\n{T}: Add X "
              "mana of any one color, where X is the greatest mana value among commanders you "
              "control.")
ACADEMY_TEXT = ("{T}: Add {C}.\nFederation — {5}, {T}: Choose a color. Add an amount of mana of "
                "that color equal to the number of creature types among non-Borg creatures you "
                "control.")


def test_command_chair_counts_the_commander():
    profile = derive(oracle(CHAIR_TEXT, name="Command Chair"), set())
    assert profile.mana_rule == {"rule": "counts", "subtype": "mv:commander", "color": "",
                                 "activation": 0}
    assert profile.review_reasons == []


def test_starfleet_academy_counts_creature_types_without_the_borg():
    profile = derive(oracle(ACADEMY_TEXT, name="Starfleet Academy",
                            type_line="Legendary Land"), set())
    assert profile.mana_rule == {"rule": "counts", "subtype": "creature_types:borg",
                                 "color": "", "activation": 5}
    assert profile.review_reasons == []


KIRK_TEXT = ("Vigilance\nSpacecraft creatures you control get +3/+3.\nWhenever Captain Kirk "
             "enters or attacks, search your library and/or graveyard for a basic land card or a "
             "card named Starship Enterprise, reveal it, and put it into your hand. If you search "
             "your library this way, shuffle.")


def kirk_oracle():
    card = OracleCard(pk=uuid.uuid4(), name="Captain Kirk, Boldly Going",
                      front_name="Captain Kirk, Boldly Going", oracle_text=KIRK_TEXT,
                      type_line="Legendary Creature — Human Officer", power="3", toughness="3",
                      mana_cost="{2}{W}", cmc=3, keywords=["Vigilance"])
    card.profile = derive(card, set(), forge=[])
    return card


def test_kirk_finds_a_basic_land_as_it_enters_and_attacks():
    profile = kirk_oracle().profile
    assert profile.land_search["battlefield"] == 0 and profile.land_search["hand"] == 1
    assert profile.land_search["basic"] and profile.land_search["when"] == "enters"
    assert profile.combat == {"triggers": [{"event": "attacks", "who": "self", "to_hand": True}]}
    assert profile.review_reasons == [] and profile.tutor_to == ""


def test_kirk_is_said_on_the_card_list_as_lands_to_hand():
    oracle_card = kirk_oracle()
    card, gaps = adapter.engine_readings([oracle_card], builtin=False)[oracle_card.pk]
    assert card.play.search.hand == 1 and card.play.search.battlefield == 0
    assert card.turn.combat == (CombatTrigger(event="attacks", who="self", to_hand=True),)
    assert [gap.field for gap in gaps if gap.field != "priority"] == ["assumed_trigger"]
    assert adapter._combat_text(card.turn.combat[0]) == "whenever it attacks: 1 basic land to hand"


QUARK_TEXT = ("You can't sacrifice Treasure tokens.\nAt the beginning of each upkeep, create a "
              "Treasure token. (It's an artifact with \"{T}, Sacrifice this token: Add one mana "
              "of any color.\")")


def test_quark_makes_four_treasures_a_round_and_locks_them():
    profile = derive(oracle(QUARK_TEXT, name="Quark, Humble Proprietor",
                            type_line="Legendary Creature — Ferengi Rogue"), set(), forge=[])
    assert profile.triggers == [{"event": "upkeep", "filter": "each", "treasures": 4}]
    assert profile.combat == {"treasure_lock": True}
    assert profile.review_reasons == []


PICARD_TEXT = ("Other creatures you control get +1/+1.\nWhenever you cast a creature or Spacecraft "
               "spell, create a 1/1 red Officer creature token.\nTap seven untapped Officers you "
               "control: Search your library for a Spacecraft card, put it onto the battlefield "
               "with seven charge counters on it, then shuffle.")


def test_picard_s_search_says_what_it_costs():
    profile = derive(oracle(PICARD_TEXT, name="Captain Jean-Luc Picard",
                            type_line="Legendary Creature — Human Officer"),
                     {"tutor", "tutor-to-battlefield", "tutor-artifact"}, forge=[])
    assert profile.review_reasons == ["tutors as an ability whose cost the engine does not pay"]


# --- the engine -------------------------------------------------------------------------

FOREST = Card("Forest", 0, 0, 0, LAND, subtypes=frozenset({"forest"}), basic=True)
MOUNTAIN = Card("Mountain", 0, 0, 0, LAND, subtypes=frozenset({"mountain"}), basic=True)
DAL = build.card("Dal R'El, Augment Orphan", 3, 0, 2, CREATURE, cost=parse("{1}{R}{G}"),
                 triggers=(Trigger("landfall", mana=1, mana_color="RG"),))
LEADER = Card("Leader", 5, 0, 5, CREATURE, cost=parse("{4}{G}"), types=frozenset({"creature"}))
CHAIR = build.card("Command Chair", 4, 0, 4, ROCK, cost=parse("{4}"), mana_abilities=(
    ManaAbility(COUNTS, subtype="mv:commander"), ManaAbility(FLAT, {"WUBRG": 3})))
# The counting ability before the plain one, as the adapter orders them.
ACADEMY = build.card("Starfleet Academy", 0, 0, 0, LAND, mana_abilities=(
    ManaAbility(COUNTS, subtype="creature_types:borg", activation_generic=5),
    ManaAbility(FLAT, {"C": 1})))


def creature(name, *types):
    return Card(name, 2, 0, 2, CREATURE, cost=parse("{2}"), types=frozenset({"creature"}),
                creature_types=frozenset(types))


def game_with(*, lands=(), rocks=(), creatures=(), hand=(), open_main=True):
    game = Game(random.Random(1), deck=DeckDefinition("v37", LEADER, (FOREST,)))
    game.library = [creature(f"Filler {n}") for n in range(10)]
    game.lands, game.rocks = list(lands), list(rocks)
    game.creatures, game.hand = list(creatures), list(hand)
    tap(game, lands=0, rocks=0)
    game.turn = 3
    if open_main:
        actions.OpenMainPhase().run(game, None)
    return game


def test_dal_rel_s_mana_is_red_or_green():
    game = game_with(lands=[FOREST], creatures=[DAL], hand=[MOUNTAIN], open_main=False)
    actions.PlayLand(index=0).run(game, None)
    assert game.pending_mana == ["RG"]
    actions.OpenMainPhase().run(game, None)
    assert game.pool.total == 3
    assert game.pool.can_pay_cost(parse("{R}{R}{G}"))


def test_command_chair_makes_three_until_a_bigger_commander_is_out():
    assert game_with(rocks=[CHAIR]).pool.total == 3
    assert game_with(rocks=[CHAIR], creatures=[LEADER]).pool.total == 5
    small = DeckDefinition("v37", Card("Small", 2, 0, 2, CREATURE, cost=parse("{1}{G}")),
                           (FOREST,))
    game = Game(random.Random(1), deck=small)
    game.lands, game.rocks, game.creatures = [], [CHAIR], [small.commander]
    tap(game, lands=0, rocks=0)
    actions.OpenMainPhase().run(game, None)
    assert game.pool.total == 3


def test_a_token_copy_of_the_commander_does_not_count():
    copy = Card("Leader", 5, 0, 5, CREATURE, types=frozenset({"creature"}), token=True)
    assert game_with(rocks=[CHAIR], creatures=[copy]).pool.total == 3


def test_starfleet_academy_pays_five_only_for_more():
    few = [creature("Kirk", "human", "officer"), creature("Spock", "vulcan", "officer")]
    assert game_with(lands=[FOREST] * 5 + [ACADEMY], creatures=few).pool.total == 6
    many = few + [creature(f"Crew {kind}", kind)
                  for kind in ("android", "klingon", "ferengi", "betazoid", "trill")]
    many.append(creature("Drone", "borg", "cyborg"))
    # Human, officer, vulcan and the five: eight, the Borg's types not among them.
    assert game_with(lands=[FOREST] * 5 + [ACADEMY], creatures=many).pool.total == 8


PLAINS = Card("Plains", 0, 0, 0, LAND, subtypes=frozenset({"plains"}), basic=True)
VAULT = Card("Vault", 0, 0, 0, LAND)
KIRK = build.card("Captain Kirk, Boldly Going", 3, 1, 2, CREATURE, cost=parse("{2}{W}"),
                  types=frozenset({"creature"}), power=3, toughness=3,
                  land_search=LandSearch(battlefield=0, hand=1, when="enters"),
                  combat=(CombatTrigger(event="attacks", who="self", to_hand=True),))


def test_kirk_s_land_goes_to_hand_a_basic_one():
    game = game_with(creatures=[KIRK])
    game.library = [VAULT, PLAINS, creature("Bear")]
    assert game.search_lands(KIRK) == 1
    assert game.hand == [PLAINS] and game.lands == [] and game.library[0] is VAULT


def test_kirk_finds_another_as_it_attacks():
    game = game_with(creatures=[KIRK])
    game.library = [PLAINS, FOREST, creature("Bear")]
    assert game.combat() is False
    assert len(game.hand) == 1 and game.hand[0].is_land and len(game.library) == 2


QUARK = build.card("Quark, Humble Proprietor", 3, 1, 2, CREATURE, cost=parse("{2}{R}"),
                   types=frozenset({"creature"}), treasure_lock=True,
                   triggers=(Trigger("upkeep", filter="each", treasures=4,
                                     treasure_mana="WUBRG"),))


def test_quark_puts_every_treasure_aside_but_a_spawn():
    game = game_with(lands=[MOUNTAIN] * 3, hand=[QUARK])
    game.treasures = ["WUBRG", "C"]
    game.pool.treasures = ["WUBRG", "C"]
    game.cast(QUARK, game.pool)
    assert game.treasures == ["C"] and game.pool.treasures == ["C"]
    assert game.locked_treasures == ["WUBRG"]


def test_quark_s_treasures_wait_until_it_is_gone():
    game = game_with(creatures=[QUARK], open_main=False)
    game.begin_turn()
    assert game.treasures == [] and len(game.locked_treasures) == 4
    actions.OpenMainPhase().run(game, None)
    assert game.pool.treasures == []
    artifact = AdditionalCost(sacrifice=frozenset({"artifact"}))
    assert TREASURE not in game.fodder(artifact, game.pool)
    saved = serial.load_game(serial.dump_game(game), game.deck)
    assert saved.locked_treasures == game.locked_treasures
    game.board_wipe()
    game.pool = None
    game.begin_turn()
    actions.OpenMainPhase().run(game, None)
    assert len(game.pool.treasures) == 4 and game.locked_treasures == []
