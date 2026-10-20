"""P19 R20 (engine version 24): mana kept as counters, energy or a Food.

Pentad Prism and Crystalline Crawler keep their counters as mana and spend
them only when a payment needs it, as a Treasure; Runaway Steam-Kin turns
three counters into {R}{R}{R}. Vivid lands, storage lands, Gemstone Mine,
Aether Hub, Servant of the Conduit, Channeler Initiate, Wall of Roots, Mana
Bloom and Gilded Goose spend one use each turn they tap. Everflowing Chalice
(kicked as an X), Astral Cornucopia and Empowered Autogenerator tap for one
mana per counter. Every assumption is said on the deck page, beside the card.
"""

import random
import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from cards import profiles
from cards.models import OracleCard
from cards.profiles import _mana_production
from decks.models import Deck, DeckCard
from simulation import serial
from simulation.cards import (
    CREATURE,
    ENCHANTMENT,
    FLAT,
    INSTANT,
    LAND,
    ROCK,
    STORE_FREE,
    STORE_SCALES,
    STORE_TAP,
    Card,
    DeckDefinition,
    ManaAbility,
    Store,
)
from simulation.cards import fields as card_fields
from simulation.game import TREASURE, Game
from simulation.mana import ManaPool, Stored, has_treasure
from simulation.manacost import parse
from simulation.trace import named
from simulations import deck_cards
from simulations.engine import adapter
from tests.engine_builders import marks, tap

ANY = "WUBRG"


def card(text, name="Test Card", type_line="Artifact", toughness=""):
    return OracleCard(name=name, front_name=name, oracle_text=text, type_line=type_line,
                      produced_mana=list(ANY), toughness=toughness)


# --- the reader -----------------------------------------------------------------


def test_pentad_prism_keeps_a_counter_for_each_colour_spent():
    reading = _mana_production(card(
        "Sunburst (This artifact enters with a charge counter on it for each color of mana "
        "spent to cast it.)\nRemove a charge counter from this artifact: Add one mana of any "
        "color.", name="Pentad Prism"))
    assert reading.notes == []
    assert reading.store == {"use": "free", "kind": "charge", "per_use": 1, "amount": 1,
                             "produces": None, "start": 0, "start_from": "colors", "kick": 0,
                             "grows": "", "empty": "", "target": False, "once": False}


def test_a_vivid_land_taps_for_its_colour_once_the_counters_are_gone():
    reading = _mana_production(card(
        "This land enters tapped with two charge counters on it.\n{T}: Add {U}.\n{T}, Remove "
        "a charge counter from this land: Add one mana of any color.", name="Vivid Creek",
        type_line="Land"))
    assert (reading.amount, reading.produces, reading.notes) == (1, {"U": 1}, [])
    assert (reading.store["use"], reading.store["start"]) == ("tap", 2)


def test_a_storage_land_is_sacrificed_when_empty():
    reading = _mana_production(card(
        "This land enters tapped with two depletion counters on it.\n{T}, Remove a depletion "
        "counter from this land: Add {B}{B}. If there are no depletion counters on this land, "
        "sacrifice it.", name="Peat Bog", type_line="Land"))
    store = reading.store
    assert (store["amount"], store["produces"], store["empty"]) == (2, {"B": 2}, "sacrifice")


def test_energy_food_and_toughness_are_read():
    hub = _mana_production(card("When this land enters, you get {E} (an energy counter).\n{T}: "
                                "Add {C}.\n{T}, Pay {E}: Add one mana of any color.",
                                name="Aether Hub", type_line="Land"))
    assert (hub.store["kind"], hub.store["start"], hub.amount) == ("energy", 1, 1)
    goose = _mana_production(card(
        "Flying\nWhen this creature enters, create a Food token.\n{1}{G}, {T}: Create a Food "
        "token.\n{T}, Sacrifice a Food: Add one mana of any color.", name="Gilded Goose",
        type_line="Creature — Bird"))
    assert (goose.store["kind"], goose.store["start"]) == ("food", 1)
    wall = _mana_production(card("Defender\nPut a -0/-1 counter on this creature: Add {G}. "
                                 "Activate only once each turn.", name="Wall of Roots",
                                 type_line="Creature — Plant Wall", toughness="5"))
    assert (wall.store["use"], wall.store["start"], wall.store["empty"]) == ("tap", 5, "dies")
    assert wall.store["once"]


def test_chalice_cornucopia_and_autogenerator_count_their_counters():
    chalice = _mana_production(card(
        "Multikicker {2} (You may pay an additional {2} any number of times as you cast this "
        "spell.)\nThis artifact enters with a charge counter on it for each time it was "
        "kicked.\n{T}: Add {C} for each charge counter on this artifact.",
        name="Everflowing Chalice"))
    assert (chalice.store["use"], chalice.store["start_from"], chalice.store["kick"]) == (
        "scales", "kicks", 2)
    cornucopia = _mana_production(card(
        "This artifact enters with X charge counters on it.\n{T}: Choose a color. Add one mana "
        "of that color for each charge counter on this artifact.", name="Astral Cornucopia"))
    assert (cornucopia.store["start_from"], cornucopia.store["produces"]) == ("x", None)
    autogenerator = _mana_production(card(
        "This artifact enters tapped.\n{T}: Put a charge counter on this artifact. Add X mana "
        "of any one color, where X is the number of charge counters on this artifact.",
        name="Empowered Autogenerator"))
    assert autogenerator.store["grows"] == "use" and autogenerator.notes == []


def test_steam_kin_crawler_and_channeler_are_read():
    kin = _mana_production(card(
        "Whenever you cast a red spell, if this creature has fewer than three +1/+1 counters "
        "on it, put a +1/+1 counter on this creature.\nRemove three +1/+1 counters from this "
        "creature: Add {R}{R}{R}.", name="Runaway Steam-Kin", type_line="Creature"))
    assert (kin.store["per_use"], kin.store["grows"], kin.store["produces"]) == (
        3, "red_spell", {"R": 3})
    crawler = _mana_production(card(
        "Converge — This creature enters with a +1/+1 counter on it for each color of mana "
        "spent to cast it.\nRemove a +1/+1 counter from this creature: Add one mana of any "
        "color.\n{T}: Put a +1/+1 counter on this creature.", name="Crystalline Crawler",
        type_line="Artifact Creature"))
    assert (crawler.store["use"], crawler.store["grows"]) == ("free", "tap")
    channeler = _mana_production(card(
        "When this creature enters, put three -1/-1 counters on target creature you "
        "control.\n{T}, Remove a -1/-1 counter from this creature: Add one mana of any color.",
        name="Channeler Initiate", type_line="Creature"))
    assert (channeler.store["start"], channeler.store["target"]) == (3, True)


def test_mirrodin_s_core_choice_of_taps_stays_a_gap():
    reading = _mana_production(card(
        "{T}: Add {C}.\n{T}: Put a charge counter on this land.\n{T}, Remove a charge counter "
        "from this land: Add one mana of any color.", name="Mirrodin's Core",
        type_line="Land"))
    assert reading.store is None and reading.amount == 1
    assert reading.notes == ["a mana ability that costs 'Remove a charge counter from this "
                             "land' is not modelled"]


# --- the engine -------------------------------------------------------------------


def filler(name="Filler"):
    return Card(name, 2, 0, 2, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}))


def game_with(lands=(), rocks=(), creatures=(), other=(), hand=()):
    game = Game(random.Random(1), deck=DeckDefinition("R20", None, (filler(),)))
    game.lands, game.rocks, game.creatures = list(lands), list(rocks), list(creatures)
    game.other_permanents, game.hand = list(other), list(hand)
    game.library = [filler(f"Filler {n}") for n in range(20)]
    tap(game, lands=0, rocks=0)
    return game


def next_turn(game):
    game.begin_turn()
    return game.open_pool().by_color()


def stored(name, store, kind=ROCK, mv=0, cost="", mana=(), types=("artifact",), colors=""):
    return Card(name, mv, 0, mv, kind, cost=parse(cost) if cost else None,
                types=frozenset(types), mana_abilities=tuple(mana), store=store,
                colors=frozenset(colors))


PRISM = stored("Pentad Prism", Store(STORE_FREE, ANY, start_from="colors"), mv=2, cost="{2}")
VIVID = stored("Vivid Creek", Store(STORE_TAP, ANY, start=2), LAND,
               mana=(ManaAbility(FLAT, {"U": 1}),), types=("land",))
HUB = stored("Aether Hub", Store(STORE_TAP, ANY, energy=True, start=1), LAND,
             mana=(ManaAbility(FLAT, {"C": 1}),), types=("land",))
SERVANT = stored("Servant of the Conduit", Store(STORE_TAP, ANY, energy=True, start=2),
                 CREATURE, mv=2, cost="{1}{G}", types=("creature",))


def test_pentad_prism_keeps_its_counters_as_mana_a_payment_spends():
    game = game_with(hand=[PRISM])
    game.cast(PRISM, ManaPool(W=1, U=1))
    assert game.treasures == [ANY, ANY]
    assert all(isinstance(key, Stored) for key in game.treasures)
    assert not has_treasure(game.treasures)
    pool = game.mana()
    pool.treasures = list(game.treasures)
    assert pool.can_pay_cost(parse("{G}{G}"))


def test_kept_counters_are_no_treasure_an_altar_may_sacrifice():
    from simulation.cards import AdditionalCost
    game = game_with()
    game.treasures = [Stored(ANY)]
    assert TREASURE not in game.fodder(AdditionalCost(sacrifice=frozenset({"artifact"})))
    game.treasures = [Stored(ANY), ANY]
    assert TREASURE in game.fodder(AdditionalCost(sacrifice=frozenset({"artifact"})))


def test_a_vivid_land_spends_a_counter_each_turn_then_taps_for_its_colour():
    game = game_with()
    game.lands = [VIVID]
    game._store_enters(VIVID)
    assert [next_turn(game) for _ in range(3)] == [{ANY: 1}, {ANY: 1}, {"U": 1}]


def test_energy_is_shared_and_runs_out():
    game = game_with()
    for each in (HUB, SERVANT):
        game._store_enters(each)
    game.lands, game.creatures = [HUB], [SERVANT]
    assert game.energy == 3
    assert next_turn(game) == {ANY: 2}
    # One energy left: the land takes it, the Servant has none to pay.
    assert next_turn(game) == {ANY: 1}
    assert next_turn(game) == {"C": 1} and game.energy == 0


def test_a_storage_land_is_sacrificed_as_the_next_turn_begins():
    bog = stored("Peat Bog", Store(STORE_TAP, "B", amount=2, start=2, empty="sacrifice"),
                 LAND, types=("land",))
    game = game_with()
    game.lands = [bog]
    game._store_enters(bog)
    assert [next_turn(game) for _ in range(2)] == [{"B": 2}, {"B": 2}]
    assert next_turn(game) == {} and bog in game.graveyard and not game.lands


def test_wall_of_roots_makes_green_five_times_then_dies():
    wall = stored("Wall of Roots", Store(STORE_TAP, "G", start=5, empty="dies"), CREATURE,
                  mv=2, cost="{1}{G}", types=("creature",))
    game = game_with()
    game.creatures = [wall]
    game._store_enters(wall)
    assert [next_turn(game) for _ in range(5)] == [{"G": 1}] * 5
    next_turn(game)
    assert wall in game.graveyard and "Wall of Roots dies" in game.log


def test_mana_bloom_enters_with_x_and_returns_to_hand():
    bloom = stored("Mana Bloom", Store(STORE_TAP, ANY, start_from="x", empty="hand"),
                   ENCHANTMENT, mv=1, cost="{X}{G}", types=("enchantment",))
    game = game_with()
    game._resolve(bloom, ManaPool(), x=2)
    assert bloom in game.other_permanents and named(game, "counters")["Mana Bloom"] == 2
    assert [next_turn(game) for _ in range(2)] == [{ANY: 1}, {ANY: 1}]
    next_turn(game)
    assert bloom in game.hand and bloom not in game.other_permanents


def test_everflowing_chalice_taps_for_one_per_kick():
    chalice = Card("Everflowing Chalice", 0, 0, 0, ROCK, cost=parse("{0}"),
                   types=frozenset({"artifact"}), x_count=2,
                   store=Store(STORE_SCALES, "C", start_from="kicks"))
    game = game_with(hand=[chalice])
    pool = ManaPool(colorless=4)
    assert game.max_x(chalice, pool) == 2
    game.cast(chalice, pool, x=2)
    assert game.mana().by_color() == {"C": 2}


def test_empowered_autogenerator_makes_one_more_each_turn():
    generator = stored("Empowered Autogenerator", Store(STORE_SCALES, "", grows="use"), mv=4)
    game = game_with(hand=[filler("Green Spell")])
    game.rocks = [generator]
    game._store_enters(generator)
    made = [next_turn(game) for _ in range(3)]
    assert [sum(each.values()) for each in made] == [1, 2, 3]
    assert all(len(each) == 1 for each in made)


def test_runaway_steam_kin_turns_three_red_spells_into_three_red():
    kin = stored("Runaway Steam-Kin", Store(STORE_FREE, "R", amount=3, per_use=3,
                                            grows="red_spell"), CREATURE, mv=2,
                 types=("creature",))
    bolt = Card("Shock", 1, 1, 0, INSTANT, cost=parse("{R}"), colors=frozenset("R"))
    game = game_with(creatures=[kin])
    game._store_enters(kin)
    for _ in range(2):
        game.spell_cast(bolt)
    assert game.treasures == [] and named(game, "counters")["Runaway Steam-Kin"] == 2
    game.spell_cast(bolt)
    assert game.treasures == ["R", "R", "R"] and named(game, "counters")["Runaway Steam-Kin"] == 0


def test_crystalline_crawler_taps_for_one_more_counter_each_turn():
    crawler = stored("Crystalline Crawler", Store(STORE_FREE, ANY, start_from="colors",
                                                         grows="tap"), CREATURE,
                     mv=4, types=("artifact", "creature"))
    game = game_with()
    game.creatures = [crawler]
    game._store_enters(crawler, colors=2)
    assert len(game.treasures) == 2
    next_turn(game)
    assert len(game.treasures) == 3


def test_a_saved_game_keeps_counters_energy_and_kept_mana():
    game = game_with()
    game.lands = [VIVID, HUB]
    game._store_enters(VIVID)
    game._store_enters(HUB)
    game.treasures = [ANY, Stored(ANY)]
    marks(game, emptied=["Peat Bog"])
    loaded = serial.load_game(serial.dump_game(game), game.deck)
    assert (named(loaded, "counters"), loaded.energy, named(loaded, "emptied")) == (
        {"Vivid Creek": 2}, 1, ["Peat Bog"])
    assert [isinstance(key, Stored) for key in loaded.treasures] == [False, True]
    assert card_fields(loaded.lands[0])["store"] == card_fields(VIVID)["store"]


# --- the adapter and the deck page --------------------------------------------------


@pytest.fixture
def store_deck(db):
    owner = get_user_model().objects.create_user(email="r20@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, cost="", toughness=""):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
            type_line=type_line, oracle_text=text, produced_mana=list(ANY), mana_cost=cost,
            toughness=toughness)
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return found

    prism = made("Pentad Prism", "Artifact", "Sunburst (This artifact enters with a charge "
                 "counter on it for each color of mana spent to cast it.)\nRemove a charge "
                 "counter from this artifact: Add one mana of any color.", "{2}")
    hub = made("Aether Hub", "Land", "When this land enters, you get {E} (an energy "
               "counter).\n{T}: Add {C}.\n{T}, Pay {E}: Add one mana of any color.")
    chalice = made("Everflowing Chalice", "Artifact", "Multikicker {2} (You may pay an "
                   "additional {2} any number of times as you cast this spell.)\nThis artifact "
                   "enters with a charge counter on it for each time it was kicked.\n{T}: Add "
                   "{C} for each charge counter on this artifact.", "{0}")
    wall = made("Wall of Roots", "Creature — Plant Wall", "Defender\nPut a -0/-1 counter on "
                "this creature: Add {G}. Activate only once each turn.", "{1}{G}", "5")
    goose = made("Gilded Goose", "Creature — Bird", "Flying\nWhen this creature enters, "
                 "create a Food token.\n{1}{G}, {T}: Create a Food token.\n{T}, Sacrifice a "
                 "Food: Add one mana of any color.", "{G}")
    deck = Deck.objects.create(owner=owner, name="Counters")
    for found in (prism, hub, chalice, wall, goose):
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, prism=prism, hub=hub, chalice=chalice,
                           wall=wall, goose=goose)


def reading_of(deck, oracle_card):
    return next(each for each in adapter.readings(deck) if each.oracle_card.pk == oracle_card.pk)


def assumed(reading):
    return [gap.text for gap in reading.gaps if gap.field == "assumed_cost"]


def test_every_card_states_its_assumption_beside_it(store_deck):
    found = {name: reading_of(store_deck.deck, getattr(store_deck, name))
             for name in ("prism", "hub", "chalice", "wall", "goose")}
    for name, reading in found.items():
        assert not reading.unreadable, name
        assert reading.card.mana.store is not None, name
        grid = deck_cards.GridCard(reading=reading, open=False, answered=False)
        assert grid.assumptions == assumed(reading) and grid.assumptions, name
    assert "as a Treasure is spent" in assumed(found["prism"])[0]
    assert "for each colour the open mana can make" in " ".join(assumed(found["prism"]))
    assert "spends one energy each turn" in assumed(found["hub"])[0]
    assert "is kicked as often as the mana left pays for" in " ".join(assumed(found["chalice"]))
    assert found["chalice"].card.x_count == 2
    wall = " ".join(assumed(found["wall"]))
    assert "once each turn" in wall and "then dies" in wall
    assert "never pays {1}{G} for another Food" in assumed(found["goose"])[0]


def test_the_deck_page_shows_the_store_s_assumption(store_deck, client):
    client.force_login(store_deck.owner)
    page = client.get(store_deck.deck.get_absolute_url()).content.decode()
    assert "Assumed:" in page
    assert "spends one energy each turn it is tapped for any colour" in page
