"""P19 R23 (engine version 27): searches with a choice, and land searches of
new shapes.

Tutors to hand find a type or a subtype up to a mana value (Open the Armory,
Solve the Equation, Spellseeker); Demonic Counsel any card with delirium;
Archdruid's Charm a land while the hand has none; Gifts Ungiven keeps the two
cards the opponent leaves; Gamble discards at random; Defense of the Heart
fires once an opponent is assumed to have three creatures. Land searches count
the greatest power (Traverse the Outlands, Entish Restoration), take Gates
(Circuitous Route), repeat (Claim Jumper), run in the opponents' end steps
(Keeper of the Accord), take a tempting offer (one opponent, assumed) or
reveal X lands (Open the Way). Neheb adds {R} for each point of combat
damage; Crime Novelist {R} for each artifact sacrificed. Every assumption is
said on the deck page.
"""

import random
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from cards import profiles
from cards.models import OracleCard
from cards.profiles import derive
from decks.models import Deck, DeckCard
from simulation import actions
from simulation.cards import (
    ARTIFACT,
    CREATURE,
    ENCHANTMENT,
    FLAT,
    INSTANT,
    OPPONENT_CREATURES,
    SACRIFICED,
    SORCERY,
    Card,
    CombatTrigger,
    DeckDefinition,
    LandSearch,
    ManaAbility,
    Trigger,
    TutorSpec,
)
from simulation.game import Game
from simulation.mana import ManaPool
from simulation.manacost import parse
from simulations import deck_cards
from simulations.engine import adapter

ANY = "WUBRG"
TO_HAND = {"tutor", "tutor-to", "tutor-to-hand"}


def oracle(text, name="Test Card", type_line="Sorcery", power="", toughness="",
           mana_cost=""):
    return OracleCard(name=name, front_name=name.split(" // ")[0], oracle_text=text,
                      type_line=type_line, produced_mana=[], power=power,
                      toughness=toughness, keywords=[], mana_cost=mana_cost)


def profile_of(text, tags=frozenset(), **kwargs):
    return derive(oracle(text, **kwargs), set(tags))


# --- the reader: tutors -----------------------------------------------------------


def test_a_search_to_hand_reads_its_types_subtypes_and_limit():
    armory = profile_of(
        "Search your library for an Aura or Equipment card, reveal it, put it into your hand, "
        "then shuffle.", TO_HAND | {"tutor-artifact", "tutor-enchantment"},
        name="Open the Armory")
    assert (armory.tutor_to, armory.tutor_count) == ("hand", 1)
    assert armory.tutor_filter == {"types": ["aura", "equipment"], "max_mv": None}
    assert armory.review_reasons == []
    seeker = profile_of(
        "When this creature enters, you may search your library for an instant or sorcery card "
        "with mana value 2 or less, reveal it, put it into your hand, then shuffle.",
        TO_HAND | {"tutor-instant", "tutor-sorcery", "tutor-mv"}, name="Spellseeker",
        type_line="Creature — Human Wizard", power="1", toughness="1")
    assert seeker.tutor_filter == {"types": ["instant", "sorcery"], "max_mv": 2}
    assert seeker.review_reasons == []


def test_searches_of_one_shape_each():
    counsel = profile_of(
        "Search your library for a Demon card, reveal it, put it into your hand, then shuffle.\n"
        "Delirium — If there are four or more card types among cards in your graveyard, "
        "instead search your library for any card, put it into your hand, then shuffle.",
        TO_HAND, name="Demonic Counsel")
    assert counsel.tutor_filter == {"types": ["demon"], "max_mv": None, "delirium_any": True}
    gifts = profile_of(
        "Search your library for up to four cards with different names and reveal them. Target "
        "opponent chooses two of those cards. Put the chosen cards into your graveyard and the "
        "rest into your hand. Then shuffle.", TO_HAND | {"tutor-to-graveyard"},
        name="Gifts Ungiven", type_line="Instant")
    assert (gifts.tutor_to, gifts.tutor_count, gifts.tutor_filter) == ("hand", 2, {"gifts": 4})
    charm = profile_of(
        "Choose one —\n• Search your library for a creature or land card and reveal it. Put it "
        "onto the battlefield tapped if it's a land card. Otherwise, put it into your hand. "
        "Then shuffle.\n• Exile target artifact or enchantment.",
        TO_HAND | {"tutor-to-battlefield"}, name="Archdruid's Charm", type_line="Instant")
    assert charm.tutor_filter["land_tapped"] and charm.tutor_filter["types"] == [
        "creature", "land"]
    heart = profile_of(
        "At the beginning of your upkeep, if an opponent controls three or more creatures, "
        "sacrifice this enchantment, search your library for up to two creature cards, put "
        "those cards onto the battlefield, then shuffle.", {"tutor", "tutor-to-battlefield"},
        name="Defense of the Heart", type_line="Enchantment")
    assert (heart.tutor_to, heart.tutor_count, heart.tutor_kind) == (
        "battlefield", 2, "creature")
    assert heart.tutor_filter["upkeep"] == OPPONENT_CREATURES
    for found in (counsel, gifts, charm, heart):
        assert found.review_reasons == [], found.oracle_card.name


def test_exile_symmetry_and_the_opponents_libraries():
    broodlord = profile_of(
        "Flying\nWhen this creature enters, search your library for a card, exile it face down, "
        "then shuffle. For as long as that card remains exiled, you may play it.",
        {"tutor", "tutor-to", "tutor-to-exile"}, name="Hoarding Broodlord",
        type_line="Creature — Dragon", power="5", toughness="5")
    assert (broodlord.tutor_to, broodlord.tutor_filter) == ("hand", {"exiled": True})
    symmetry = profile_of(
        "Choose two target players. Each of them searches their library for a card, then "
        "shuffles and puts that card on top.", {"tutor", "tutor-to", "tutor-to-top"},
        name="Scheming Symmetry")
    assert symmetry.tutor_to == "top" and symmetry.tutor_filter["symmetry"]
    agent = profile_of(
        "Flash\nYou control your opponents while they're searching their libraries.",
        {"tutor", "tutor-to", "tutor-to-exile"}, name="Opposition Agent",
        type_line="Creature — Human Rogue", power="3", toughness="2")
    grasp = profile_of(
        "Search target opponent's library for a card and exile it face down. Then that player "
        "shuffles. You may play that card for as long as it remains exiled.", {"tutor"},
        name="Praetor's Grasp")
    for found in (agent, grasp):
        assert found.tutor_to == "" and found.tutor_filter == {"opponents": True}
    gamble = profile_of(
        "Search your library for a card, put that card into your hand, discard a card at "
        "random, then shuffle.", TO_HAND, name="Gamble")
    assert gamble.tutor_filter == {"random_discard": 1}
    for found in (broodlord, symmetry, agent, grasp, gamble):
        assert found.review_reasons == [], found.oracle_card.name


# --- the reader: land searches ------------------------------------------------------


def search_of(text, **kwargs):
    found = profile_of(text, {"tutor", "tutor-to", "tutor-to-battlefield"},
                       **kwargs)
    assert found.review_reasons == [], found.review_reasons
    return {key: value for key, value in found.land_search.items()
            if value not in (0, False, "", [])}


def test_land_searches_of_new_shapes():
    assert search_of(
        "Search your library for up to two basic land cards and/or Gate cards, put them onto "
        "the battlefield tapped, then shuffle.", name="Circuitous Route") == {
        "battlefield": 2, "tapped": True, "basic": True, "when": "cast", "also": ["gate"]}
    assert search_of(
        "Sacrifice a land. Search your library for up to two basic land cards, put them onto "
        "the battlefield tapped, then shuffle. If you control a creature with power 4 or "
        "greater, instead search your library for up to three basic land cards, put them onto "
        "the battlefield tapped, then shuffle.", name="Entish Restoration",
        type_line="Instant") == {
        "battlefield": 2, "tapped": True, "basic": True, "when": "cast",
        "sacrifices_land": True, "power_at": 4, "power_count": 3}
    assert search_of(
        "Search your library for up to X basic land cards, where X is the greatest power among "
        "creatures you control. Put those cards onto the battlefield tapped, then shuffle.",
        name="Traverse the Outlands")["by_power"]
    jumper = search_of(
        "Vigilance\nWhen this creature enters, if an opponent controls more lands than you, you "
        "may search your library for a Plains card and put it onto the battlefield tapped. "
        "Then if an opponent controls more lands than you, repeat this process once. If you "
        "search your library this way, shuffle.", name="Claim Jumper",
        type_line="Creature — Rabbit Mercenary", power="3", toughness="3")
    assert jumper["repeat"] == 1 and jumper["condition"] == "opponent_more_lands"
    keeper = search_of(
        "At the beginning of each opponent's end step, if that player controls more lands than "
        "you, you may search your library for a basic Plains card, put it onto the battlefield "
        "tapped, then shuffle.", name="Keeper of the Accord",
        type_line="Creature — Human Soldier", power="3", toughness="4")
    assert keeper["when"] == "opponents" and keeper["types"] == ["plains"]
    tempt = search_of(
        "Tempting offer — Search your library for a land card and put it onto the battlefield. "
        "Each opponent may search their library for a land card and put it onto the "
        "battlefield. For each opponent who searches a library this way, search your library "
        "for a land card and put it onto the battlefield. Then each player who searched a "
        "library this way shuffles.", name="Tempt with Discovery")
    assert tempt == {"battlefield": 1, "when": "cast", "tempting": 1}
    way = search_of(
        "X can't be greater than the number of players in the game.\nReveal cards from the top "
        "of your library until you reveal X land cards. Put those land cards onto the "
        "battlefield tapped and the rest on the bottom of your library in a random order.",
        name="Open the Way", mana_cost="{X}{G}{G}")
    assert way == {"tapped": True, "when": "cast", "by_x": True, "reveal": True, "x_max": 4}


def test_sword_of_hearth_and_home_neheb_and_crime_novelist():
    sword = profile_of(
        "Equipped creature gets +2/+2 and has protection from green and from white.\nWhenever "
        "equipped creature deals combat damage to a player, exile up to one target creature you "
        "own, then search your library for a basic land card. Put both cards onto the "
        "battlefield under your control, then shuffle.\nEquip {2}",
        {"tutor", "tutor-to", "tutor-to-battlefield"},
        name="Sword of Hearth and Home", type_line="Artifact — Equipment")
    assert sword.combat == {"triggers": [{"event": "damage", "who": "equipped", "search": True,
                                          "untapped": True, "flicker": True}], "equip": "{2}"}
    assert sword.review_reasons == [] and sword.land_search is None
    neheb = profile_of(
        "Afflict 3\nAt the beginning of each of your postcombat main phases, add {R} for each 1 "
        "life your opponents have lost this turn.", name="Neheb, the Eternal",
        type_line="Legendary Creature — Zombie Minotaur Warrior", power="4", toughness="6")
    assert neheb.combat == {"triggers": [{"event": "damage", "who": "total", "mana": "R"}]}
    assert neheb.review_reasons == []
    novelist = profile_of(
        "Whenever you sacrifice an artifact, put a +1/+1 counter on this creature and add {R}.",
        name="Crime Novelist", type_line="Creature — Goblin Bard", power="3", toughness="2")
    assert novelist.triggers == [{"event": "sacrificed", "filter": "artifact", "mana": 1,
                                  "color": "R", "counter": 1}]
    assert novelist.review_reasons == []


# --- the engine ---------------------------------------------------------------------


def creature(name, power=2, mv=2, types=("human",)):
    return Card(name, mv, 0, mv, CREATURE, cost=parse(f"{{{mv}}}"), types=frozenset({"creature"}),
                creature_types=frozenset(types), power=power, toughness=2)


def spell(name, kind=SORCERY, mv=2, subtypes=(), tutor=None, land_search=None, **kwargs):
    return Card(name, mv, 0, mv, kind, cost=parse(f"{{{mv}}}"), types=frozenset({kind}),
                printed_subtypes=frozenset(subtypes), tutor=tutor, land_search=land_search,
                **kwargs)


def land(name="Forest", basic=True, subtypes=("forest",), color="G"):
    return Card(name, 0, 0, 0, "land", basic=basic, subtypes=frozenset(subtypes),
                types=frozenset({"land"}), mana_abilities=(ManaAbility(FLAT, {color: 1}),))


FOREST, PLAINS = land(), land("Plains", subtypes=("plains",), color="W")
GATE = land("Basilisk Gate", basic=False, subtypes=("gate",), color="C")
BEAR = creature("Bear")


def game_with(library=(), hand=(), creatures=(), lands=(), other=(), graveyard=(), turn=0):
    game = Game(random.Random(3), deck=DeckDefinition("R23", None, (BEAR,)))
    game.library, game.hand = list(library), list(hand)
    game.creatures, game.lands, game.rocks = list(creatures), list(lands), []
    game.other_permanents, game.graveyard = list(other), list(graveyard)
    game.tapped_lands = game.tapped_rocks = 0
    game.turn = turn
    return game


def resolve(game, card, x=0):
    actions._apply_cast_effect(game, card, None, x)


def test_a_search_finds_a_subtype_up_to_a_mana_value():
    sword = spell("Sword", ARTIFACT, 3, subtypes=("equipment",))
    armory = spell("Open the Armory", tutor=TutorSpec(types=frozenset({"aura", "equipment"})))
    game = game_with(library=[creature("Big", mv=7), sword])
    resolve(game, armory)
    assert game.hand == [sword]
    small = spell("Small", INSTANT, 2)
    big = spell("Big", INSTANT, 3)
    seeker = TutorSpec(types=frozenset({"instant", "sorcery"}), max_mv=2)
    game = game_with(library=[big, small, creature("Elf", mv=1)])
    resolve(game, replace(creature("Spellseeker"), tutor=seeker))
    assert game.hand == [small]


def test_demonic_counsel_finds_any_card_with_delirium():
    demon = creature("Demon", mv=4, types=("demon",))
    big = creature("Titan", mv=9)
    counsel = spell("Demonic Counsel", tutor=TutorSpec(types=frozenset({"demon"}),
                                                       delirium_any=True))
    game = game_with(library=[big, demon])
    resolve(game, counsel)
    assert game.hand == [demon]
    yard = [FOREST, BEAR, spell("Bolt", INSTANT), spell("Rock", ARTIFACT)]
    game = game_with(library=[demon, big], graveyard=yard)
    assert game.delirium()
    resolve(game, counsel)
    assert game.hand == [big]


def test_archdruids_charm_finds_a_land_while_the_hand_has_none():
    charm = spell("Archdruid's Charm", INSTANT, 3, tutor=TutorSpec(
        types=frozenset({"creature", "land"}), land_tapped=True))
    game = game_with(library=[creature("Titan", mv=6), FOREST])
    resolve(game, charm)
    assert game.lands == [FOREST] and game.tapped_lands == 1 and game.hand == []
    titan = creature("Titan", mv=6)
    game = game_with(library=[titan, FOREST], hand=[PLAINS])
    resolve(game, charm)
    assert game.hand == [PLAINS, titan] and game.lands == []


def test_gifts_ungiven_keeps_the_two_the_opponent_leaves():
    cards = [creature(f"C{mv}", mv=mv) for mv in (1, 5, 3, 7, 2)]
    gifts = spell("Gifts Ungiven", INSTANT, 4, tutor=TutorSpec(count=2, gifts=4))
    game = game_with(library=cards)
    resolve(game, gifts)
    assert [card.name for card in game.graveyard] == ["C7", "C5"]
    assert [card.name for card in game.hand] == ["C3", "C2"]


def test_gamble_discards_at_random():
    gamble = spell("Gamble", mv=1, tutor=TutorSpec(random_discard=1))
    game = game_with(library=[creature("Titan", mv=9)], hand=[BEAR, PLAINS])
    resolve(game, gamble)
    assert len(game.hand) == 2 and len(game.graveyard) == 1
    assert "at random" in game.log[-1]


def test_defense_of_the_heart_fires_from_turn_four():
    heart = spell("Defense of the Heart", ENCHANTMENT, 4, tutor=TutorSpec(
        to_hand=False, to_battlefield=True, count=2, kind="creature",
        upkeep=OPPONENT_CREATURES))
    titans = [creature("Titan", mv=9), creature("Wurm", mv=8), creature("Elf", mv=1)]
    game = game_with(library=[FOREST] * 4 + titans, other=[heart], turn=2)
    resolve(game, heart)
    assert game.creatures == []
    actions.BeginTurn().run(game, None)
    assert game.creatures == [] and heart in game.other_permanents
    actions.BeginTurn().run(game, None)
    assert {card.name for card in game.creatures} == {"Titan", "Wurm"}
    assert heart in game.graveyard


def test_land_searches_by_power_with_gates_and_a_sacrifice():
    route = spell("Circuitous Route", 4, land_search=LandSearch(
        battlefield=2, also=frozenset({"gate"})))
    game = game_with(library=[GATE, creature("X")])
    resolve(game, route)
    assert game.lands == [GATE]
    entish = spell("Entish Restoration", INSTANT, 3, land_search=LandSearch(
        battlefield=2, sacrifices_land=True, power_at=4, power_count=3))
    game = game_with(library=[FOREST] * 5, lands=[PLAINS])
    resolve(game, entish)
    assert len(game.lands) == 2 and PLAINS in game.graveyard
    game = game_with(library=[FOREST] * 5, lands=[PLAINS], creatures=[creature("Big", 4)])
    resolve(game, entish)
    assert len(game.lands) == 3
    traverse = spell("Traverse the Outlands", 5, land_search=LandSearch(by_power=True))
    game = game_with(library=[FOREST] * 9, creatures=[creature("Big", 5), BEAR])
    game.plus_counters = {"Big": 1}
    resolve(game, traverse)
    assert len(game.lands) == 6


def test_claim_jumper_searches_twice_while_behind():
    jumper = replace(creature("Claim Jumper", 3, 3), land_search=LandSearch(
        basic=False, types=frozenset({"plains"}), when="enters",
        condition="opponent_more_lands", repeat=1))
    game = game_with(library=[PLAINS] * 4, turn=5, lands=[FOREST])
    resolve(game, jumper)
    assert len(game.lands) == 3
    game = game_with(library=[PLAINS] * 4, turn=3, lands=[FOREST])
    resolve(game, jumper)
    # Turn 3: the opponents have two lands; one Plains catches up.
    assert len(game.lands) == 2


def test_keeper_of_the_accord_searches_in_the_opponents_end_steps():
    keeper = replace(creature("Keeper of the Accord", 3, 4), land_search=LandSearch(
        types=frozenset({"plains"}), when="opponents", condition="opponent_more_lands"))
    game = game_with(library=[PLAINS] * 5 + [BEAR] * 5, creatures=[keeper], lands=[FOREST],
                     turn=3)
    game.begin_turn()
    # Turn 4: the opponents have three lands each, you one; two Plains.
    assert len(game.lands) == 3 and game.tapped_lands == 0


def test_tempt_with_discovery_and_open_the_way():
    tempt = spell("Tempt with Discovery", 4, land_search=LandSearch(
        basic=False, tapped=False, tempting=1))
    game = game_with(library=[FOREST] * 4)
    resolve(game, tempt)
    assert len(game.lands) == 2
    way = spell("Open the Way", 2, x_count=1, x_max=4, land_search=LandSearch(
        basic=False, by_x=True, reveal=True))
    game = game_with(library=[BEAR, FOREST, BEAR, FOREST, FOREST, BEAR])
    resolve(game, way, x=2)
    assert len(game.lands) == 2 and game.tapped_lands == 2
    assert [card.name for card in game.library] == ["Forest", "Bear", "Bear", "Bear"]
    assert game_with().max_x(way, ManaPool(G=20)) == 4


def test_sword_of_hearth_and_home_finds_an_untapped_land():
    sword = Card("Sword of Hearth and Home", 3, 0, 3, ARTIFACT, types=frozenset({"artifact"}),
                 equip=parse("{2}"), combat=(CombatTrigger(event="damage", who="equipped",
                                                           search=True, untapped=True),))
    game = game_with(library=[FOREST] * 3, creatures=[BEAR], other=[sword])
    game.attached = {sword.name: BEAR.name}
    game.pool = ManaPool()
    game.combat()
    assert len(game.lands) == 1 and game.pool.total == 1


def test_neheb_adds_red_for_each_point_of_combat_damage():
    neheb = replace(creature("Neheb, the Eternal", 4, 5), combat=(
        CombatTrigger(event="damage", who="total", mana="R"),))
    game = game_with(creatures=[neheb, creature("Bear", 2)])
    game.pool = ManaPool()
    assert game.combat_expected()
    assert game.combat() is True
    assert game.pool.total == 6


def test_crime_novelist_sees_a_treasure_spent():
    novelist = replace(creature("Crime Novelist", 3, 3), triggers=(
        Trigger(event=SACRIFICED, filter="artifact", mana=1, mana_color="R", counter=1),))
    game = game_with(creatures=[novelist])
    game.treasures = [ANY]
    pool = ManaPool()
    pool.treasures = [ANY]
    game.pool = pool
    game.cast(spell("Thing", mv=1), pool)
    assert game.treasures == [] and pool.total == 1
    assert game.power(novelist) == 4


# --- the deck page --------------------------------------------------------------------


@pytest.fixture
def search_deck(db):
    owner = get_user_model().objects.create_user(email="r23@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, cost, power="", toughness=""):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name.split(" // ")[0],
            search_name=name.lower(), type_line=type_line, oracle_text=text,
            produced_mana=[], mana_cost=cost, power=power, toughness=toughness, keywords=[])
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return found

    cards = SimpleNamespace(
        tempt=made("Tempt with Discovery", "Sorcery",
                   "Tempting offer — Search your library for a land card and put it onto the "
                   "battlefield. Each opponent may search their library for a land card and put "
                   "it onto the battlefield. For each opponent who searches a library this way, "
                   "search your library for a land card and put it onto the battlefield. Then "
                   "each player who searched a library this way shuffles.", "{3}{G}"),
        gifts=made("Gifts Ungiven", "Instant",
                   "Search your library for up to four cards with different names and reveal "
                   "them. Target opponent chooses two of those cards. Put the chosen cards into "
                   "your graveyard and the rest into your hand. Then shuffle.", "{3}{U}"),
        heart=made("Defense of the Heart", "Enchantment",
                   "At the beginning of your upkeep, if an opponent controls three or more "
                   "creatures, sacrifice this enchantment, search your library for up to two "
                   "creature cards, put those cards onto the battlefield, then shuffle.",
                   "{3}{G}"),
        agent=made("Opposition Agent", "Creature — Human Rogue",
                   "Flash\nYou control your opponents while they're searching their libraries.",
                   "{2}{B}", "3", "2"),
        neheb=made("Neheb, the Eternal", "Legendary Creature — Zombie Minotaur Warrior",
                   "Afflict 3\nAt the beginning of each of your postcombat main phases, add {R} "
                   "for each 1 life your opponents have lost this turn.", "{3}{R}{R}", "4", "6"),
    )
    deck = Deck.objects.create(owner=owner, name="Searches")
    for found in vars(cards).values():
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, **vars(cards))


def reading_of(deck, oracle_card):
    return next(each for each in adapter.readings(deck) if each.oracle_card.pk == oracle_card.pk)


def assumed(reading):
    return " ".join(gap.text for gap in reading.gaps if gap.field.startswith("assumed_"))


def test_every_card_states_its_assumption_beside_it(search_deck):
    said = {
        "tempt": "one opponent takes the tempting offer",
        "gifts": "the two best of the four cards",
        "heart": "three creatures from your fourth turn on",
        "agent": "it does nothing here",
        "neheb": "life lost any other way is not counted",
    }
    for name, words in said.items():
        reading = reading_of(search_deck.deck, getattr(search_deck, name))
        assert not reading.unreadable, name
        assert words in assumed(reading), name
        grid = deck_cards.GridCard(reading=reading, open=False, answered=False)
        assert grid.assumptions, name
    tempt = reading_of(search_deck.deck, search_deck.tempt)
    assert "one more for each opponent who searches too" in tempt.searches
    gifts = reading_of(search_deck.deck, search_deck.gifts)
    assert gifts.searches == "4 cards, 2 of them to hand and the rest to the graveyard"
    heart = reading_of(search_deck.deck, search_deck.heart)
    assert "three creatures" in heart.searches
    neheb = reading_of(search_deck.deck, search_deck.neheb)
    assert "{R}" in neheb.triggers
