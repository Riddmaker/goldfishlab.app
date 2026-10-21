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

from simulation import agent, build, serial
from simulation.cards import (
    CREATURE,
    FILTER,
    FLAT,
    MULTIPLY,
    PER_CONTROLLED,
    AdditionalCost,
    Card,
    CombatTrigger,
    CostReduction,
    DeckDefinition,
    EndStepSpec,
    Growth,
    LandSearch,
    ManaAbility,
    SpellFilter,
    Store,
    TapMana,
    TappedUnless,
    Trigger,
    TutorSpec,
    UpkeepSpec,
)
from simulation.fixtures import chainer
from simulation.game import Game
from simulation.manacost import Hybrid, ManaCost
from simulation.rules import mana, play
from simulation.trace import named
from tests.engine_builders import hold, marks

#: The rules of a card with every field set away from its default, as the
#: fields it is made with (`simulation.build.MadeWith`), including one of
#: each nested dataclass.
RULES = {
    "enters_tapped": True,
    "mana_abilities": (
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
    "ritual_gain": 3,
    "ritual_color": "R",
    "cost_reduction": CostReduction(amount=2, requires_pip=False, color="U"),
    "draw_on_cast": 2,
    "life_on_cast": 1,
    "tutor": TutorSpec(to_hand=False, count=3, life=3, kind=CREATURE, to_battlefield=True,
                    color="G", max_mv=4, max_mv_x=True, to_top=True,
                    types=frozenset({"artifact", "enchantment"}), max_mv_sacrificed=2),
    "upkeep": UpkeepSpec(draw=2, life=1, life_per_mv=True),
    "end_step": EndStepSpec(max_hand=6, life_floor=20),
    "skips_draw_step": True,
    "untaps": False,
    "land_search": LandSearch(battlefield=2, hand=1, tapped=False, basic=False,
                           types=frozenset({"forest", "island"}), life=1, when="play",
                           sacrifice=True, untap_at=4,
                           cost=ManaCost(pips=(("G", 1),), generic=2), taps=True,
                           share_type=True, each=True, condition="opponent_more_lands",
                           sacrifices_land=True, land_cost_types=frozenset({"forest"}),
                           sacrifice_other=frozenset({"creature"}), discard=1),
    "tapped_unless": TappedUnless(kind="lands", types=frozenset({"island"}), count=3,
                               at_least=False, other=True, basic=True, type="island",
                               legendary=True),
    "treasures": 2,
    "treasure_mana": "RG",
    "landers": 1,
    "discard_cost": 1,
    "discard_on_cast": 2,
    "put_back_on_cast": 2,
    "draws_x": True,
    "ritual_counts": "tapped:island",
    "additional_costs": (AdditionalCost(sacrifice=frozenset({"artifact", "creature"}),
                                     sacrifice_filter="G", life=3, life_x=True, discard=1,
                                     mana=ManaCost(generic=2), exile_from_graveyard="creature"),
                      AdditionalCost(life=5)),
    "sacrifice_mana": AdditionalCost(sacrifice=frozenset({"creature"}), sacrifice_filter="goblin"),
    "sacrifice_mana_amount": 2,
    "sacrifice_mana_color": "B",
    "sacrifice_mana_taps": True,
    "sacrifice_mana_other": True,
    "triggers": (Trigger("cast", filter="instant_sorcery", treasures=1, treasure_mana="C",
                      mana=1, mana_color="R", draw=1, life=1),),
    "exiled_on_cast": True,
    "tap_mana": TapMana(types=frozenset({"creature"}), filter="U", mana="C", amount=2,
                     spend_only=(SpellFilter(types=frozenset({"artifact"})),), tokens=1),
    "mox": "imprint",
    "mana_mills": 1,
    "store": Store("free", "R", amount=3, per_use=3, start_from="colors", grows="red_spell",
                empty="dies"),
    "growth": Growth(enters=1, enters_x=True, grows=("evolve", "enters:elf"), adapt=3,
                  adapt_cost=ManaCost(pips=(("G", 2),), generic=3), bonus=1, puts=2,
                  draws_on_greatest=True, look=4),
    "flip": 4,
    "combat": (CombatTrigger(event="damage", who="any", types=frozenset({"ninja"}), treasures=1,
                          treasure_mana="WUBRG", mana="RG", search=True),),
    "equip": ManaCost(generic=2),
    "germ": True,
    "treasure_bonus": 1,
}

#: A card with **every** field set away from its default. The point of it is
#: to fail loudly when a new field or a new field type arrives that
#: `serial.load` does not know how to rebuild.
EVERYTHING = build.card(
    name="Everything At Once",
    mv=5,
    pips=2,
    generic=3,
    kind=CREATURE,
    engine_roles=frozenset({"draw_engine", "recursive"}),
    land_type="coffers",
    goldfish_castable=False,
    needs_creature_in_yard=True,
    needs_creature_on_bf=True,
    priority=91,
    accelerant=True,
    subtypes=frozenset({"swamp", "forest"}),
    cost=ManaCost(pips=(("B", 2),), generic=3, colorless=1,
                  hybrid=(Hybrid(colors=("W", "U")),), phyrexian=("G",),
                  has_x=True),
    basic=True,
    x_count=3,
    x_min=2,
    x_max=4,
    creature_types=frozenset({"elf"}),
    legendary=True,
    colors=frozenset({"B", "G"}),
    defender=True,
    enchants="forest",
    printed_subtypes=frozenset({"elf", "druid"}),
    power=4,
    toughness=5,
    haste=True,
    token=True,
    **RULES,
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
    # Printed, the play hints and the blocks (card model phase 5); not the
    # fields it was made with.
    for name, _annotation in serial.fields_of(Card):
        assert name in dumped, f"{name} never reached the row"
    assert dumped["abilities"] and "mana_abilities" not in dumped


def test_a_card_stored_in_the_old_form_comes_back_the_same():
    """A playtest stored before phase 5 wrote a card as the fields it was
    made with; its deck must load as the same card."""
    printed = {item.name: getattr(EVERYTHING, item.name) for item in dataclasses.fields(Card)
               if item.name != "abilities"}
    old = {"_type": "Card", **{name: serial.dump(value)
                               for name, value in {**printed, **RULES}.items()}}

    assert serial.load(Card, json.loads(json.dumps(old))) == EVERYTHING


def test_a_set_inside_an_effect_comes_back_as_a_set():
    """An effect's arguments are typed `Any`: the dump has to say a set is one."""
    found = roundtrip(EVERYTHING)

    assert found.abilities == EVERYTHING.abilities
    assert any(isinstance(value, frozenset) for ability in found.abilities
               for effect in ability.effects for _key, value in effect.args)


def test_no_field_of_the_exhaustive_card_was_left_at_its_default():
    """Otherwise the guard above passes while testing nothing."""
    for field in dataclasses.fields(Card):
        default = field.default
        if default is dataclasses.MISSING or field.name == "abilities":
            continue
        assert getattr(EVERYTHING, field.name) != default, (
            f"{field.name} is still at its default, so it is not being tested")
    for name, default in {**mana.FIELDS, **play.FIELDS}.items():
        assert RULES[name] != default, (
            f"{name} is still at its default, so it is not being tested")


def test_a_frozenset_does_not_come_back_as_a_list():
    """`Card` is hashable and compared by value; a list would break both."""
    assert isinstance(roundtrip(EVERYTHING).subtypes, frozenset)
    assert isinstance(roundtrip(EVERYTHING).abilities, tuple)


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
    game.rocks = [EVERYTHING]
    hold(game, EVERYTHING)
    restored = serial.load_game(json.loads(json.dumps(serial.dump_game(game))),
                                chainer.DECK)
    assert restored.stays_tapped == [EVERYTHING]


def test_a_chosen_colour_survives_the_trip():
    """Caged Sun chose once (P19 R13); a reload must not choose again."""
    game = Game(random.Random(3), deck=chainer.DECK)
    marks(game, chosen_colors={"Caged Sun": "G"})
    assert named(restored(game), "chosen_colors") == {"Caged Sun": "G"}


def test_the_creatures_that_arrived_survive_the_trip():
    """A creature's {T} waits a turn (P19 R15); a reload keeps that."""
    game = Game(random.Random(3), deck=chainer.DECK)
    marks(game, arrived=["Wight of the Reliquary"], tapped_creatures=["Knight"])
    back = restored(game)
    assert (named(back, "arrived"), named(back, "tapped_creatures")) == (
        ["Wight of the Reliquary"], ["Knight"])


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


# --- Card objects after a restore (card model, phase 4) -----------------------


def test_a_restored_commander_is_still_the_commander():
    """The engine asks "is this the commander" by identity: a restored game of
    new card objects offered its commander as sacrifice fodder."""
    game = Game(random.Random(3), deck=chainer.DECK)
    game.creatures = [chainer.DECK.commander]
    back = restored(game)
    assert back.creatures[0] is chainer.DECK.commander
    cost = AdditionalCost(sacrifice=frozenset({"creature"}))
    assert chainer.DECK.commander not in back.fodder(cost)


def test_each_copy_of_a_card_comes_back_as_its_own_object():
    game = played()
    back = restored(game)
    swamps = [card for card in back.library if card.name == "Swamp"]
    assert len({id(card) for card in swamps}) == len(swamps) > 1


def test_a_token_the_game_made_stays_its_own_card():
    germ = Card(name="Germ", mv=0, pips=0, generic=0, kind=CREATURE, token=True)
    game = Game(random.Random(3), deck=chainer.DECK)
    game.creatures = [germ]
    assert restored(game).creatures == [germ]


def test_the_cards_drawn_survive_the_trip():
    game = played()
    assert restored(game).drawn == game.drawn
