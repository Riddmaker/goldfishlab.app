"""Engine state through a database row and back.

A playtest session is stored as a deck snapshot plus the actions taken, so
these two conversions sit under every game anybody ever plays. The tests that
matter are not the happy paths - they are the ones that fail when somebody adds
a field to `Card` and forgets this module exists.

No Django here, like the engine tests beside it.
"""

import dataclasses
import json
import random

import pytest

from simulation import agent, serial
from simulation.cards import (
    CREATURE,
    FILTER,
    FLAT,
    MULTIPLY,
    PER_CONTROLLED,
    AdditionalCost,
    Card,
    CostReduction,
    DeckDefinition,
    EndStepSpec,
    LandSearch,
    ManaAbility,
    SpellFilter,
    TapMana,
    TappedUnless,
    Trigger,
    TutorSpec,
    UpkeepSpec,
)
from simulation.fixtures import chainer
from simulation.game import Game
from simulation.manacost import Hybrid, ManaCost

#: A card with **every** field set away from its default, including one of each
#: nested dataclass. The point of it is to fail loudly when a new field or a new
#: field type arrives that `serial.load` does not know how to rebuild.
EVERYTHING = Card(
    name="Everything At Once",
    mv=5,
    pips=2,
    generic=3,
    kind=CREATURE,
    tags=frozenset({"ramp", "draw"}),
    land_type="coffers",
    enters_tapped=True,
    goldfish_castable=False,
    needs_creature_in_yard=True,
    needs_creature_on_bf=True,
    mana_abilities=(
        ManaAbility(rule=FLAT, produces=(("B", 1), ("C", 1))),
        ManaAbility(rule=PER_CONTROLLED, activation_generic=2,
                    subtype="swamp", color="G"),
        ManaAbility(rule=FLAT, produces=(("C", 2),),
                    only_if=TappedUnless(kind="artifacts", count=3)),
        ManaAbility(rule=FILTER, produces=(("WB", 2),), pays_with="WB"),
        ManaAbility(rule=MULTIPLY, subtype="permanent", times=2),
        ManaAbility(rule=FLAT, produces=(("WUBRG", 1),), spend_only=(
            SpellFilter(types=frozenset({"creature"}), subtypes=frozenset({"elf"}),
                        legendary=True, colorless=True, multicolored=True, noncreature=True,
                        only_color="G"),)),
    ),
    ritual_gain=3,
    ritual_color="R",
    cost_reduction=CostReduction(amount=2, requires_pip=False, color="U"),
    draw_on_cast=2,
    life_on_cast=1,
    tutor=TutorSpec(to_hand=False, count=3, life=3, kind=CREATURE, to_battlefield=True,
                    color="G", max_mv=4, max_mv_x=True, to_top=True,
                    types=frozenset({"artifact", "enchantment"}), max_mv_sacrificed=2),
    upkeep=UpkeepSpec(draw=2, life=1, life_per_mv=True),
    end_step=EndStepSpec(max_hand=6, life_floor=20),
    skips_draw_step=True,
    priority=91,
    accelerant=True,
    subtypes=frozenset({"swamp", "forest"}),
    cost=ManaCost(pips=(("B", 2),), generic=3, colorless=1,
                  hybrid=(Hybrid(colors=("W", "U")),), phyrexian=("G",),
                  has_x=True),
    untaps=False,
    land_search=LandSearch(battlefield=2, hand=1, tapped=False, basic=False,
                           types=frozenset({"forest", "island"}), life=1, when="play",
                           sacrifice=True, untap_at=4,
                           cost=ManaCost(pips=(("G", 1),), generic=2), taps=True,
                           share_type=True, each=True, condition="opponent_more_lands",
                           sacrifices_land=True, land_cost_types=frozenset({"forest"}),
                           sacrifice_other=frozenset({"creature"}), discard=1),
    basic=True,
    tapped_unless=TappedUnless(kind="lands", types=frozenset({"island"}), count=3,
                               at_least=False, other=True, basic=True, type="island",
                               legendary=True),
    treasures=2,
    treasure_mana="RG",
    landers=1,
    discard_cost=1,
    discard_on_cast=2,
    put_back_on_cast=2,
    x_count=3,
    x_min=2,
    draws_x=True,
    creature_types=frozenset({"elf"}),
    legendary=True,
    colors=frozenset({"B", "G"}),
    defender=True,
    enchants="forest",
    ritual_counts="tapped:island",
    additional_costs=(AdditionalCost(sacrifice=frozenset({"artifact", "creature"}),
                                     sacrifice_filter="G", life=3, life_x=True, discard=1,
                                     mana=ManaCost(generic=2), exile_from_graveyard="creature"),
                      AdditionalCost(life=5)),
    sacrifice_mana=AdditionalCost(sacrifice=frozenset({"creature"}), sacrifice_filter="goblin"),
    sacrifice_mana_amount=2,
    sacrifice_mana_color="B",
    sacrifice_mana_taps=True,
    sacrifice_mana_other=True,
    triggers=(Trigger("cast", filter="instant_sorcery", treasures=1, treasure_mana="C",
                      mana=1, mana_color="R", draw=1, life=1),),
    exiled_on_cast=True,
    printed_subtypes=frozenset({"elf", "druid"}),
    power=4,
    tap_mana=TapMana(types=frozenset({"creature"}), filter="U", mana="C", amount=2,
                     spend_only=(SpellFilter(types=frozenset({"artifact"})),), tokens=1),
    mox="imprint",
    mana_mills=1,
)


def roundtrip(value, annotation=Card):
    """Through `json` and back, so nothing survives on object identity."""
    return serial.load(annotation, json.loads(json.dumps(serial.dump(value))))


# --- Cards -----------------------------------------------------------------

def test_a_card_with_every_field_set_survives_the_trip():
    """The guard for the whole module: nothing is dropped, nothing is reshaped."""
    assert roundtrip(EVERYTHING) == EVERYTHING


def test_every_field_of_a_card_is_actually_written_down():
    """Equality would still hold if a field came back as its default."""
    dumped = serial.dump(EVERYTHING)
    for field in dataclasses.fields(Card):
        assert field.name in dumped, f"{field.name} never reached the row"


def test_no_field_of_the_exhaustive_card_was_left_at_its_default():
    """Otherwise the guard above passes while testing nothing."""
    for field in dataclasses.fields(Card):
        default = field.default
        if default is dataclasses.MISSING:
            continue
        assert getattr(EVERYTHING, field.name) != default, (
            f"{field.name} is still at its default, so it is not being tested")


def test_a_frozenset_does_not_come_back_as_a_list():
    """`Card` is hashable and compared by value; a list would break both."""
    assert isinstance(roundtrip(EVERYTHING).subtypes, frozenset)
    assert isinstance(roundtrip(EVERYTHING).mana_abilities, tuple)


def test_a_stored_type_nobody_knows_is_refused():
    """Rows are input too, even the ones we wrote."""
    data = serial.dump(EVERYTHING)
    data["_type"] = "os.system"
    with pytest.raises(ValueError, match="unknown engine type"):
        serial.load(Card, data)


# --- Decks -----------------------------------------------------------------

def test_the_reference_deck_survives_the_trip():
    stored = json.loads(json.dumps(serial.dump_deck(chainer.DECK)))
    assert serial.load_deck(stored) == chainer.DECK


def test_a_deck_without_a_commander_survives_it_too():
    """`commander: Card | None` - the union branch nothing else exercises."""
    deck = DeckDefinition(name="No general", commander=None,
                          library=chainer.DECK.library)
    assert serial.load_deck(json.loads(json.dumps(serial.dump_deck(deck)))) == deck


# --- Games -----------------------------------------------------------------

def played(seed: int = 11, turns: int = 3) -> Game:
    game = Game(random.Random(seed))
    game.take_opening_hand()
    for _ in range(turns):
        agent.take_turn(game)
    return game


def restored(game: Game) -> Game:
    return serial.load_game(json.loads(json.dumps(serial.dump_game(game))),
                            chainer.DECK)


def test_a_game_comes_back_with_every_zone_intact():
    game = played()
    back = restored(game)
    for zone in serial.GAME_ZONES:
        assert getattr(back, zone) == getattr(game, zone), zone


def test_a_game_comes_back_with_its_log_and_its_life():
    game = played()
    back = restored(game)
    assert back.log == game.log
    assert (back.life, back.turn, back.mulligans) == (
        game.life, game.turn, game.mulligans)


def test_floating_mana_survives_the_trip():
    """A pool that came back empty would hand the player a free spell."""
    game = played()
    assert serial.dump_game(game)["pool"] is not None
    assert restored(game).pool.by_color() == game.pool.by_color()


def test_a_game_that_has_not_started_has_no_pool():
    game = Game(random.Random(2))
    game.take_opening_hand()
    assert restored(game).pool is None


def test_the_generator_comes_back_where_it_was():
    """`Game.__init__` shuffles, so restoring the state too early loses it."""
    game = played()
    assert restored(game).rng.getstate() == game.rng.getstate()


def test_a_restored_game_plays_on_exactly_as_the_original_would():
    """The property replay rests on. Everything else here only supports it."""
    game = played()
    back = restored(game)
    for _ in range(3):
        agent.take_turn(game)
        agent.take_turn(back)
    assert back.log == game.log
    assert back.life == game.life
    assert back.permanent_names == game.permanent_names


def test_a_permanent_that_stays_tapped_survives_the_trip():
    """Mana Vault's "already used" is state a replay has to reproduce.

    Engine version 3 added `Game.stays_tapped`; a cached state that dropped it
    would untap the Vault on the next request and hand the player three mana
    they already spent.
    """
    game = Game(random.Random(3), deck=chainer.DECK)
    game.stays_tapped = [EVERYTHING]
    restored = serial.load_game(json.loads(json.dumps(serial.dump_game(game))),
                                chainer.DECK)
    assert restored.stays_tapped == [EVERYTHING]


def test_a_state_cached_before_version_three_still_loads():
    game = Game(random.Random(3), deck=chainer.DECK)
    data = json.loads(json.dumps(serial.dump_game(game)))
    del data["stays_tapped"]
    assert serial.load_game(data, chainer.DECK).stays_tapped == []


def test_a_chosen_colour_survives_the_trip():
    """Caged Sun chose once (P19 R13); a reload must not choose again."""
    game = Game(random.Random(3), deck=chainer.DECK)
    game.chosen_colors = {"Caged Sun": "G"}
    assert restored(game).chosen_colors == {"Caged Sun": "G"}
    data = json.loads(json.dumps(serial.dump_game(game)))
    del data["chosen_colors"]
    assert serial.load_game(data, chainer.DECK).chosen_colors == {}


def test_the_creatures_that_arrived_survive_the_trip():
    """A creature's {T} waits a turn (P19 R15); a reload keeps that."""
    game = Game(random.Random(3), deck=chainer.DECK)
    game.arrived, game.tapped_creatures = ["Wight of the Reliquary"], ["Knight"]
    back = restored(game)
    assert (back.arrived, back.tapped_creatures) == (["Wight of the Reliquary"], ["Knight"])


def test_mana_waiting_for_the_pool_survives_the_trip():
    """Lotus Cobra's mana for the land drop waits for the main phase (P19 R16)."""
    game = Game(random.Random(3), deck=chainer.DECK)
    game.pending_mana, game.spells_this_turn = ["G", "UR"], 2
    back = restored(game)
    assert (back.pending_mana, back.spells_this_turn) == (["G", "UR"], 2)


def test_a_lander_survives_the_trip():
    """Lander tokens wait for their mana (P19 R14); a reload keeps them."""
    game = Game(random.Random(3), deck=chainer.DECK)
    game.landers = 2
    assert restored(game).landers == 2
    data = json.loads(json.dumps(serial.dump_game(game)))
    del data["landers"]
    assert serial.load_game(data, chainer.DECK).landers == 0
