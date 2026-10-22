"""Engine v37: the Star Trek cards the engine could not read.

What these tests hold:

1. **Dal R'El**: "add {R} or {G}" as a land enters is one mana of either,
   the choice kept open until it is spent.
2. **Command Chair**: three mana of one colour, or as much as your
   commander's mana value while it is on the battlefield - whichever is more.
3. **Starfleet Academy**: {C}, or for {5} as much mana as the creature types
   among your creatures, the Borg left out.
"""

import random

from cards.models import OracleCard
from cards.profiles import derive
from simulation import actions, build
from simulation.cards import COUNTS, CREATURE, FLAT, LAND, ROCK, Card, DeckDefinition, Trigger
from simulation.game import Game
from simulation.manacost import parse
from simulation.spec import ManaAbility
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
