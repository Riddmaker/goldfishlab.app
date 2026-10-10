"""P19 R19 (engine version 23): tapping other permanents for mana, the Moxen.

Springleaf Drum and the lands like it tap a creature besides themselves,
Relic of Legends a legendary one, Urza each artifact - his Construct
included - and Grand Architect each blue creature, itself too. Only a
permanent that makes no mana is tapped, and a goldfish plays no combat: the
deck page says so beside the card. Mox Diamond is cast with a land card to
spare, Chrome Mox exiles the most expensive coloured card and makes its
colours, Millikin mills a card each time it taps.
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
    ARTIFACT,
    CREATURE,
    DISCARD_LAND,
    FLAT,
    IMPRINT,
    LAND,
    ROCK,
    Card,
    DeckDefinition,
    ManaAbility,
    SpellFilter,
    TapMana,
    TappedUnless,
)
from simulation.game import Game
from simulation.manacost import parse
from simulations import deck_cards
from simulations.engine import adapter

ANY = "WUBRG"


def card(text, name="Test Card", type_line="Artifact", produced=("W", "U", "B", "R", "G")):
    return OracleCard(name=name, front_name=name, oracle_text=text, type_line=type_line,
                      produced_mana=list(produced))


# --- the reader -----------------------------------------------------------------


def test_springleaf_drum_needs_a_creature_to_tap():
    reading = _mana_production(card("{T}, Tap an untapped creature you control: Add one "
                                    "mana of any color."))
    assert (reading.amount, reading.notes) == (1, [])
    assert reading.condition == {"if": {"kind": "tap_fodder", "types": ["creature"],
                                        "legendary": False, "type": ""},
                                 "otherwise": None}


def test_survivors_encampment_taps_for_colourless_without_one():
    reading = _mana_production(card("{T}: Add {C}.\n{T}, Tap an untapped creature you "
                                    "control: Add one mana of any color.",
                                    type_line="Land", produced=ANY + "C"))
    assert reading.condition["otherwise"] == {"amount": 1, "produces": {"C": 1},
                                              "activation": 0}


def test_relic_of_legends_taps_each_legendary_creature_beside_its_own_tap():
    reading = _mana_production(card("{T}: Add one mana of any color.\nTap an untapped "
                                    "legendary creature you control: Add one mana of any "
                                    "color.", name="Relic of Legends"))
    assert (reading.amount, reading.notes) == (1, [])
    assert reading.tap_mana == {"types": ["creature"], "filter": "legendary", "amount": 1,
                                "produces": None, "spend_only": None, "token": 0}


def test_urza_taps_each_artifact_and_counts_his_construct():
    reading = _mana_production(card(
        "When Urza enters, create a 0/0 colorless Construct artifact creature token with "
        "\"This token gets +1/+1 for each artifact you control.\"\nTap an untapped artifact "
        "you control: Add {U}.\n{5}: Shuffle your library, then exile the top card.",
        name="Urza, Lord High Artificer", type_line="Legendary Creature — Human Artificer",
        produced=("U",)))
    assert reading.tap_mana["types"] == ["artifact"]
    assert (reading.tap_mana["produces"], reading.tap_mana["token"]) == ({"U": 1}, 1)
    assert reading.notes == []


def test_grand_architect_s_mana_pays_for_artifact_spells_only():
    reading = _mana_production(card(
        "Other blue creatures you control get +1/+1.\nTap an untapped blue creature you "
        "control: Add {C}{C}. Spend this mana only to cast artifact spells or activate "
        "abilities of artifacts.", type_line="Creature — Vedalken Wizard", produced=("C",)))
    assert reading.tap_mana["filter"] == "U" and reading.tap_mana["amount"] == 2
    assert reading.tap_mana["spend_only"][0]["types"] == ["artifact"]


def test_a_creature_type_an_artifact_or_creature_and_nontoken_are_read():
    seton = _mana_production(card("Tap an untapped Druid you control: Add {G}.",
                                  type_line="Legendary Creature — Centaur Druid",
                                  produced=("G",)))
    assert (seton.tap_mana["types"], seton.tap_mana["filter"]) == (["creature"], "druid")
    stalwart = _mana_production(card("{T}, Tap an untapped artifact or creature you "
                                     "control: Add one mana of any color.",
                                     type_line="Creature — Cat Druid"))
    assert stalwart.condition["if"]["types"] == ["artifact", "creature"]
    meria = _mana_production(card("Tap an untapped nontoken artifact you control: Add one "
                                  "mana of any color.", type_line="Legendary Creature"))
    assert meria.tap_mana["filter"] == ""


def test_tapping_two_permanents_stays_a_gap():
    reading = _mana_production(card("Tap two untapped Elves you control: Add {G}.",
                                    type_line="Creature — Elf Druid", produced=("G",)))
    assert reading.amount is None and reading.tap_mana is None
    assert reading.notes == [
        "a mana ability that costs 'Tap two untapped Elves you control' is not modelled"]


def test_mox_diamond_and_chrome_mox_are_read():
    diamond = _mana_production(card("If this artifact would enter, you may discard a land "
                                    "card instead. If you do, put this artifact onto the "
                                    "battlefield. If you don't, put it into its owner's "
                                    "graveyard.\n{T}: Add one mana of any color."))
    assert (diamond.mox, diamond.amount, diamond.notes) == ("discard_land", 1, [])
    chrome = _mana_production(card("Imprint — When this artifact enters, you may exile a "
                                   "nonartifact, nonland card from your hand.\n{T}: Add one "
                                   "mana of any of the exiled card's colors."))
    assert (chrome.mox, chrome.amount, chrome.notes) == ("imprint", 1, [])


def test_millikin_mills_a_card_for_its_mana():
    reading = _mana_production(card("{T}, Mill a card: Add {C}.", type_line="Artifact "
                                    "Creature — Construct", produced=("C",)))
    assert (reading.amount, reading.produces, reading.mills, reading.notes) == (
        1, {"C": 1}, 1, [])


# --- the engine -------------------------------------------------------------------


def creature(name, *, legendary=False, colors="G", mana=(), creature_types=("elf",)):
    return Card(name, 2, 0, 2, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}),
                legendary=legendary, colors=frozenset(colors), mana_abilities=tuple(mana),
                creature_types=frozenset(creature_types))


def taps_creature(name="Springleaf Drum", kind=ROCK, otherwise=()):
    return Card(name, 1, 0, 1, kind, types=frozenset({"artifact" if kind == ROCK else kind}),
                mana_abilities=(ManaAbility(FLAT, {ANY: 1}, only_if=TappedUnless(
                    "tap_fodder", types=frozenset({"creature"}))), *otherwise))


DRUM = taps_creature()
ENCAMPMENT = taps_creature("Survivors' Encampment", LAND, (ManaAbility(FLAT, {"C": 1}),))
ELF = creature("Llanowar Elves", mana=(ManaAbility(FLAT, {"G": 1}),))
BEAR = creature("Grizzly Bears")
RELIC = Card("Relic of Legends", 3, 0, 3, ROCK, types=frozenset({"artifact"}),
             mana_abilities=(ManaAbility(FLAT, {ANY: 1}),),
             tap_mana=TapMana(types=frozenset({"creature"}), filter="legendary", mana=ANY))


def game_with(lands=(), rocks=(), creatures=(), other=(), hand=(), library=()):
    game = Game(random.Random(1), deck=DeckDefinition("R19", None, (creature("Filler"),)))
    game.lands, game.rocks, game.creatures = list(lands), list(rocks), list(creatures)
    game.other_permanents, game.hand = list(other), list(hand)
    game.library = list(library)
    game.tapped_lands = game.tapped_rocks = 0
    return game


def test_the_drum_taps_a_creature_that_makes_no_mana():
    assert game_with(rocks=[DRUM], creatures=[BEAR]).mana().by_color() == {ANY: 1}
    assert game_with(rocks=[DRUM]).mana().by_color() == {}


def test_a_mana_creature_taps_for_its_own_mana_not_for_the_drum():
    assert game_with(rocks=[DRUM], creatures=[ELF]).mana().by_color() == {"G": 1}


def test_two_cards_never_tap_the_same_creature():
    game = game_with(lands=[ENCAMPMENT], rocks=[DRUM], creatures=[BEAR])
    assert game.mana().by_color() == {ANY: 1, "C": 1}
    beside = game_with(lands=[ENCAMPMENT], rocks=[DRUM], creatures=[BEAR, creature("Bear 2")])
    assert beside.mana().by_color() == {ANY: 2}


def test_relic_of_legends_makes_one_more_for_each_legendary_creature():
    commander = creature("Captain Sisay", legendary=True)
    game = game_with(rocks=[RELIC], creatures=[commander, creature("Another", legendary=True),
                                               BEAR])
    assert game.mana().by_color() == {ANY: 3}


def test_the_narrowest_ask_takes_its_creature_first():
    # Only one creature is legendary: Relic gets it, the Drum the other one.
    game = game_with(rocks=[DRUM, RELIC], creatures=[creature("Sisay", legendary=True), BEAR])
    assert game.mana().by_color() == {ANY: 3}


def test_urza_taps_each_artifact_and_his_construct():
    urza = Card("Urza, Lord High Artificer", 4, 0, 2, CREATURE, types=frozenset({"creature"}),
                legendary=True,
                tap_mana=TapMana(types=frozenset({"artifact"}), mana="U", tokens=1))
    bauble = Card("Mishra's Bauble", 0, 0, 0, ARTIFACT, types=frozenset({"artifact"}))
    assert game_with(creatures=[urza], other=[bauble]).mana().by_color() == {"U": 2}


def test_grand_architect_taps_itself_for_artifact_mana():
    architect = Card("Grand Architect", 3, 0, 1, CREATURE, types=frozenset({"creature"}),
                     colors=frozenset({"U"}), creature_types=frozenset({"vedalken"}),
                     tap_mana=TapMana(types=frozenset({"creature"}), filter="U", mana="C",
                                      amount=2,
                                      spend_only=(SpellFilter(types=frozenset({"artifact"})),)))
    pool = game_with(creatures=[architect, BEAR]).mana()
    assert pool.total == 0
    assert [each.made for each in pool.restricted] == [{"C": 2}]


def test_a_creature_tapped_for_mana_cannot_pay_another_tap_this_turn():
    game = game_with(rocks=[DRUM], creatures=[BEAR])
    game.arrived = []
    game.open_pool()
    assert "Grizzly Bears" in game.tapped_creatures
    assert not game.creature_can_tap(BEAR)
    # The pool asked again in the same turn still sees the Drum's creature.
    assert game.mana().by_color() == {ANY: 1}


def test_mox_diamond_needs_a_land_card_and_discards_a_tapped_one_first():
    diamond = Card("Mox Diamond", 0, 0, 0, ROCK, types=frozenset({"artifact"}),
                   mox=DISCARD_LAND, mana_abilities=(ManaAbility(FLAT, {ANY: 1}),))
    game = game_with(hand=[diamond])
    assert not game.can_cast(diamond, game.mana())
    forest = Card("Forest", 0, 0, 0, LAND, basic=True)
    tapped = Card("Temple", 0, 0, 0, LAND, enters_tapped=True)
    game = game_with(hand=[diamond, forest, tapped])
    pool = game.mana()
    assert game.can_cast(diamond, pool)
    game.cast(diamond, pool)
    assert game.hand == [forest] and game.graveyard == [tapped]
    assert diamond in game.rocks


def test_chrome_mox_exiles_the_most_expensive_coloured_card_and_makes_its_colours():
    chrome = Card("Chrome Mox", 0, 0, 0, ROCK, types=frozenset({"artifact"}), mox=IMPRINT)
    sol_ring = Card("Sol Ring", 1, 0, 1, ROCK, types=frozenset({"artifact"}))
    game = game_with(hand=[chrome, sol_ring])
    assert not game.can_cast(chrome, game.mana())
    big = Card("Big Gold", 6, 0, 4, CREATURE, cost=parse("{4}{B}{G}"),
               types=frozenset({"creature"}), colors=frozenset({"B", "G"}))
    game = game_with(hand=[chrome, BEAR, big])
    pool = game.mana()
    game.cast(chrome, pool)
    assert game.exiled == [big] and game.imprinted == {"Chrome Mox": "BG"}
    assert game.mana().by_color() == {"BG": 1}


def test_millikin_mills_a_card_when_the_pool_opens():
    millikin = Card("Millikin", 2, 0, 2, CREATURE, types=frozenset({"artifact", "creature"}),
                    mana_abilities=(ManaAbility(FLAT, {"C": 1}),), mana_mills=1)
    top = creature("Top Card")
    game = game_with(creatures=[millikin], library=[top, BEAR])
    game.open_pool()
    assert game.graveyard == [top] and game.library == [BEAR]


def test_a_saved_game_keeps_the_imprint_and_the_tapped_creatures():
    game = game_with(rocks=[DRUM], creatures=[BEAR])
    game.imprinted = {"Chrome Mox": "R"}
    game.open_pool()
    loaded = serial.load_game(serial.dump_game(game), game.deck)
    assert loaded.imprinted == {"Chrome Mox": "R"}
    assert loaded.fodder_used == ["Grizzly Bears"]


# --- the adapter and the deck page --------------------------------------------------


def test_the_card_page_says_what_the_drum_taps():
    text = adapter._ability_text(DRUM.mana_abilities[0])
    assert "if you control an untapped creature to tap for it" in text


@pytest.fixture
def drum_deck(db):
    owner = get_user_model().objects.create_user(email="r19@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, produced=(), cost=""):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
            type_line=type_line, oracle_text=text, produced_mana=list(produced),
            mana_cost=cost)
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return found

    drum = made("Springleaf Drum", "Artifact", "{T}, Tap an untapped creature you control: "
                "Add one mana of any color.", ANY, cost="{1}")
    relic = made("Relic of Legends", "Artifact", "{T}: Add one mana of any color.\nTap an "
                 "untapped legendary creature you control: Add one mana of any color.", ANY,
                 cost="{3}")
    diamond = made("Mox Diamond", "Artifact", "If this artifact would enter, you may "
                   "discard a land card instead. If you do, put this artifact onto the "
                   "battlefield. If you don't, put it into its owner's graveyard.\n{T}: Add "
                   "one mana of any color.", ANY)
    chrome = made("Chrome Mox", "Artifact", "Imprint — When this artifact enters, you may "
                  "exile a nonartifact, nonland card from your hand.\n{T}: Add one mana of "
                  "any of the exiled card's colors.", ANY)
    millikin = made("Millikin", "Artifact Creature — Construct", "{T}, Mill a card: Add {C}.",
                    "C", cost="{2}")
    elf = made("Elvish Visionary", "Creature — Elf Shaman", "", cost="{1}{G}")
    deck = Deck.objects.create(owner=owner, name="Drums")
    for found in (drum, relic, diamond, chrome, millikin, elf):
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, drum=drum, relic=relic, diamond=diamond,
                           chrome=chrome, millikin=millikin)


def reading_of(deck, oracle_card):
    return next(each for each in adapter.readings(deck) if each.oracle_card.pk == oracle_card.pk)


def assumed(reading):
    return [gap.text for gap in reading.gaps if gap.field == "assumed_cost"]


def test_every_card_states_its_assumption_beside_it(drum_deck):
    found = {name: reading_of(drum_deck.deck, getattr(drum_deck, name))
             for name in ("drum", "relic", "diamond", "chrome", "millikin")}
    for name, reading in found.items():
        assert not reading.unreadable, name
        grid = deck_cards.GridCard(reading=reading, open=False, answered=False)
        assert grid.assumptions == assumed(reading) and grid.assumptions, name
    assert "the engine plays no combat" in assumed(found["drum"])[0]
    assert "your commander included" in assumed(found["relic"])[0]
    assert "after the turn's land drop" in assumed(found["diamond"])[0]
    assert "the most expensive one" in assumed(found["chrome"])[0]
    assert "mills a card each time" in assumed(found["millikin"])[0]
    assert found["relic"].card.tap_mana.mana == "G"
    assert found["diamond"].card.mox == DISCARD_LAND


def test_the_deck_page_shows_the_drum_s_assumption(drum_deck, client):
    client.force_login(drum_deck.owner)
    page = client.get(drum_deck.deck.get_absolute_url()).content.decode()
    assert "Assumed:" in page
    assert "that makes no mana and is not used otherwise this turn" in page
