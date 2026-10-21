"""P19 R22 (engine version 26): combat brings Treasure, mana and lands.

A goldfish has no blockers. Every creature that can attacks each turn - not
the turn it arrives unless it has haste, not one that makes mana or was
tapped for something else - and deals its combat damage to a player. Only the
triggers are played: Sword of the Animist's land, Goldspan Dragon's Treasure
(which then makes two), Savage Ventmaw's mana for the second main phase,
Ragavan's and Professional Face-Breaker's Treasure, Kodama of the West Tree's
land for each modified creature, Curse of Opulence's Gold. Equipment is
equipped with mana nothing else wants; an Aura goes on a creature as it is
cast. Every assumption is said on the deck page.
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
from simulation import actions, agent, serial
from simulation.cards import (
    ARTIFACT,
    CREATURE,
    ENCHANTMENT,
    FLAT,
    GERM,
    GRANT,
    LANDFALL,
    SORCERY,
    Card,
    CombatTrigger,
    DeckDefinition,
    ManaAbility,
    Trigger,
)
from simulation.cards import fields as card_fields
from simulation.game import Game
from simulation.mana import ManaPool
from simulation.manacost import parse
from simulation.trace import named
from simulations import deck_cards
from simulations.engine import adapter
from tests.engine_builders import marks, tap

ANY = "WUBRG"


def oracle(text, name="Test Card", type_line="Creature — Human Warrior", power="2",
           toughness="2", keywords=()):
    return OracleCard(name=name, front_name=name.split(" // ")[0], oracle_text=text,
                      type_line=type_line, produced_mana=[], power=power,
                      toughness=toughness, keywords=list(keywords))


def read(text, **kwargs):
    profile = derive(oracle(text, **kwargs), set())
    return profile.combat, profile.review_reasons, profile


# --- the reader -----------------------------------------------------------------


def test_sword_of_the_animist_searches_as_the_equipped_creature_attacks():
    combat, reasons, profile = read(
        "Equipped creature gets +1/+1.\nWhenever equipped creature attacks, you may search your "
        "library for a basic land card, put it onto the battlefield tapped, then shuffle.\n"
        "Equip {2}", name="Sword of the Animist", type_line="Legendary Artifact — Equipment",
        power="", toughness="")
    assert combat == {"triggers": [{"event": "attacks", "who": "equipped", "search": True}],
                      "equip": "{2}"}
    assert reasons == [] and profile.land_search is None


def test_goldspan_dragon_makes_treasure_that_makes_two():
    combat, reasons, _ = read(
        "Flying, haste\nWhenever this creature attacks or becomes the target of a spell, create "
        "a Treasure token.\nTreasures you control have \"{T}, Sacrifice this artifact: Add two "
        "mana of any one color.\"", name="Goldspan Dragon", type_line="Creature — Dragon")
    assert combat == {"triggers": [{"event": "attacks", "who": "self", "treasures": 1}],
                      "treasure_bonus": 1}
    assert reasons == []


def test_curse_of_opulence_and_kodama():
    combat, reasons, _ = read(
        "Enchant player\nWhenever enchanted player is attacked, create a Gold token. Each "
        "opponent attacking that player does the same. (A Gold token is an artifact with "
        "\"Sacrifice this token: Add one mana of any color.\")", name="Curse of Opulence",
        type_line="Enchantment — Aura Curse", power="", toughness="")
    assert combat == {"triggers": [{"event": "attacks", "who": "curse", "treasures": 1}]}
    assert reasons == []
    combat, reasons, _ = read(
        "Reach\nModified creatures you control have trample. (Equipment, Auras you control, and "
        "counters are modifications.)\nWhenever a modified creature you control deals combat "
        "damage to a player, search your library for a basic land card, put it onto the "
        "battlefield tapped, then shuffle.", name="Kodama of the West Tree")
    assert combat == {"triggers": [{"event": "damage", "who": "modified", "search": True}]}
    assert reasons == []


def test_granted_triggers_on_equipment_and_auras():
    combat, reasons, _ = read(
        "Indestructible\nEquipped creature gets +1/+1 and has \"Whenever this creature attacks, "
        "create a Treasure token.\"\nEquip {2}", name="Diamond Pick-Axe",
        type_line="Artifact — Equipment", power="", toughness="")
    assert combat == {"triggers": [{"event": "attacks", "who": "equipped", "treasures": 1}],
                      "equip": "{2}"}
    assert reasons == []
    combat, reasons, _ = read(
        "Enchant creature\nEnchanted creature has menace and \"Whenever this creature deals "
        "combat damage to a player, create a Treasure token.\"\nWhen enchanted creature dies, "
        "draw a card.", name="Sticky Fingers", type_line="Enchantment — Aura", power="",
        toughness="")
    assert combat == {"triggers": [{"event": "damage", "who": "enchanted", "treasures": 1}],
                      "aura": True}
    assert reasons == []


def test_living_weapon_and_paradise_mantle():
    combat, reasons, _ = read(
        "Living weapon (When this Equipment enters, create a 0/0 black Phyrexian Germ creature "
        "token, then attach this to it.)\nEquipped creature gets +1/+1.\nWhenever equipped "
        "creature attacks, you may search your library for a basic land card, put it onto the "
        "battlefield tapped, then shuffle.\nEquip {3}", name="Bitterthorn, Nissa's Animus",
        type_line="Legendary Artifact — Equipment", power="", toughness="")
    assert combat["germ"] is True and combat["equip"] == "{3}" and reasons == []
    combat, reasons, profile = read(
        "Equipped creature has \"{T}: Add one mana of any color.\"\nEquip {1}",
        name="Paradise Mantle", type_line="Artifact — Equipment", power="", toughness="")
    assert combat == {"equip": "{1}"} and reasons == []
    assert profile.kind == "artifact"
    assert profile.mana_rule["subtype"] == "equipped"


def test_ragavan_thief_and_guild_artisan():
    combat, reasons, _ = read(
        "Whenever Ragavan deals combat damage to a player, create a Treasure token and exile "
        "the top card of that player's library. Until end of turn, you may cast that card.\n"
        "Dash {1}{R}", name="Ragavan, Nimble Pilferer")
    assert combat == {"triggers": [{"event": "damage", "who": "self", "treasures": 1,
                                    "extra": True}]}
    assert reasons == []
    combat, _, _ = read(
        "Ninjutsu {1}{U}\nWhenever one or more Ninja or Rogue creatures you control deal combat "
        "damage to a player, create a Treasure token.", name="Prosperous Thief")
    assert combat["triggers"] == [{"event": "damage", "who": "any", "types": ["ninja", "rogue"],
                                   "treasures": 1}]
    combat, _, _ = read(
        "Commander creatures you own have \"Whenever this creature attacks a player, if no "
        "opponent has more life than that player, you create two Treasure tokens.\"",
        name="Guild Artisan", type_line="Legendary Enchantment — Background", power="",
        toughness="")
    assert combat["triggers"] == [{"event": "attacks", "who": "commander", "treasures": 2,
                                   "most_life": True}]


def test_enters_or_attacks_and_a_vehicle():
    combat, reasons, profile = read(
        "Trample\nWhenever this creature enters or attacks, you may search your library for a "
        "basic land card, put it onto the battlefield tapped, then shuffle.",
        name="Primeval Herald")
    assert combat == {"triggers": [{"event": "attacks", "who": "self", "search": True}]}
    assert profile.land_search["when"] == "enters" and reasons == []
    combat, reasons, profile = read(
        "This Vehicle's power is equal to the number of lands you control.\nWhenever this "
        "Vehicle enters or attacks, you may search your library for a basic land card, put it "
        "onto the battlefield tapped, then shuffle.\nCrew 4", name="Lumbering Worldwagon",
        type_line="Artifact — Vehicle", power="*", toughness="4")
    assert combat == {"vehicle": True} and profile.land_search["when"] == "enters"
    assert reasons == []


def test_mana_in_combat_without_keeping_it_is_not_read():
    combat, reasons, _ = read("Whenever Radha attacks, you may add {R}{R}.\n{T}: Add {G}.",
                              name="Radha, Heir to Keld")
    assert combat is None


# --- the engine -------------------------------------------------------------------


def creature(name, power=2, toughness=2, combat=(), mana=(), haste=False, types=("human",),
             mv=2, cost="{1}{R}"):
    return Card(name, mv, 0, mv, CREATURE, cost=parse(cost), types=frozenset({"creature"}),
                creature_types=frozenset(types), power=power, toughness=toughness,
                combat=tuple(combat), mana_abilities=tuple(mana), haste=haste,
                colors=frozenset("R"))


def treasure_on(event="attacks", who="self", count=1):
    return CombatTrigger(event=event, who=who, treasures=count, treasure_mana=ANY)


FOREST = Card("Forest", 0, 0, 0, "land", basic=True, subtypes=frozenset({"forest"}),
              mana_abilities=(ManaAbility(FLAT, {"G": 1}),))
MOUNTAIN = Card("Mountain", 0, 0, 0, "land", basic=True, subtypes=frozenset({"mountain"}),
                mana_abilities=(ManaAbility(FLAT, {"R": 1}),))
SWORD = Card("Sword of the Animist", 2, 0, 2, ARTIFACT, cost=parse("{2}"),
             types=frozenset({"artifact"}), equip=parse("{2}"),
             combat=(CombatTrigger(event="attacks", who="equipped", search=True),))
LANNERY = creature("Captain Lannery Storm", combat=(treasure_on(),), haste=True, mv=3)
BEAR = creature("Bear")


def game_with(creatures=(), other=(), hand=(), library=(), lands=(), commander=None):
    game = Game(random.Random(1), deck=DeckDefinition("R22", commander, (BEAR,)))
    game.creatures, game.other_permanents = list(creatures), list(other)
    game.hand = list(hand)
    game.library = list(library) or [FOREST] * 5 + [creature(f"Filler {n}") for n in range(20)]
    game.lands, game.rocks = list(lands), []
    tap(game, lands=0, rocks=0)
    return game


def test_who_attacks():
    elf = creature("Llanowar Elves", 1, 1, mana=(ManaAbility(FLAT, {"G": 1}),))
    wall = Card("Wall", 2, 0, 2, CREATURE, types=frozenset({"creature"}), defender=True)
    new = creature("Newcomer")
    game = game_with(creatures=[BEAR, elf, wall, new, LANNERY])
    marks(game, arrived=["Newcomer", "Captain Lannery Storm"])
    assert [each.name for each in game.attackers()] == ["Bear", "Captain Lannery Storm"]
    marks(game, tapped_creatures=["Bear"])
    assert [each.name for each in game.attackers()] == ["Captain Lannery Storm"]


def test_lannery_storm_attacks_with_haste_and_makes_a_treasure():
    game = game_with(creatures=[LANNERY])
    marks(game, arrived=[LANNERY.name])
    game.pool = ManaPool()
    assert game.combat() is True
    assert game.treasures == [ANY] and game.pool.treasures == [ANY]
    assert game.combat() is False and game.treasures == [ANY]


def test_a_treasure_needs_an_attacker():
    game = game_with(creatures=[creature("Ragavan", combat=(treasure_on("damage"),))])
    marks(game, arrived=["Ragavan"])
    game.pool = ManaPool()
    assert game.combat() is False and game.treasures == []


def test_sword_of_the_animist_is_equipped_and_finds_a_land():
    game = game_with(creatures=[BEAR], other=[SWORD])
    pool = ManaPool(R=2)
    assert actions.equips(game, pool) == [actions.Equip(zone=actions.OTHER, index=0)]
    game.equip(SWORD, pool)
    assert named(game, "attached") == {SWORD.name: "Bear"} and pool.total == 0
    assert not game.can_equip(SWORD, ManaPool(R=2))
    game.pool = pool
    game.combat()
    assert [land.name for land in game.lands] == ["Forest"] and game.tapped_lands == 1


def test_kodama_finds_a_land_for_each_modified_creature():
    kodama = creature("Kodama of the West Tree", 3, 3, types=("spirit",), combat=(
        CombatTrigger(event="damage", who="modified", search=True),))
    countered, plain = creature("Countered"), creature("Plain")
    game = game_with(creatures=[kodama, countered, plain, BEAR], other=[SWORD])
    marks(game, plus_counters={"Countered": 1})
    marks(game, attached={SWORD.name: "Bear"})
    game.pool = ManaPool()
    game.combat()
    # The Bear's Sword finds one more: three lands.
    assert len(game.lands) == 3


def test_goldspan_dragon_makes_each_treasure_two():
    goldspan = creature("Goldspan Dragon", 4, 4, combat=(treasure_on(),), haste=True, mv=5)
    goldspan = replace(goldspan, treasure_bonus=1)
    game = game_with(creatures=[goldspan])
    game.begin_turn()
    pool = game.open_pool()
    game.pool = pool
    assert pool.treasure_bonus == 1
    game.combat()
    assert pool.can_pay_cost(parse("{1}{R}")) and not pool.can_pay_cost(parse("{2}{R}"))


def test_ventmaw_mana_waits_for_the_second_main_phase():
    ventmaw = creature("Savage Ventmaw", 4, 4, mv=6, cost="{4}{R}{G}", combat=(
        CombatTrigger(event="attacks", who="self", mana="RRRGGG"),))
    big = creature("Big Spell", 6, 6, mv=6, cost="{4}{R}{R}", types=())
    game = game_with(creatures=[ventmaw], hand=[big])
    game.library = [creature(f"Filler {n}") for n in range(20)]
    agent.take_turn(game)
    assert any(each.name == "Big Spell" for each in game.creatures)
    assert game.phase == actions.END


def test_an_x_spell_waits_for_combat_mana():
    ventmaw = creature("Savage Ventmaw", 4, 4, mv=6, cost="{4}{R}{G}", combat=(
        CombatTrigger(event="attacks", who="self", mana="RRRGGG"),))
    blaze = Card("Blaze", 1, 0, 0, SORCERY, cost=parse("{X}{R}"), x_count=1,
                 types=frozenset({"sorcery"}), colors=frozenset("R"))
    game = game_with(creatures=[ventmaw], hand=[blaze], lands=[MOUNTAIN])
    game.library = [creature(f"Filler {n}") for n in range(20)]
    agent.take_turn(game)
    assert "  -> X = 6" in game.log


def test_curse_of_opulence_makes_gold_when_you_attack():
    curse = Card("Curse of Opulence", 1, 0, 1, ENCHANTMENT, types=frozenset({"enchantment"}),
                 combat=(treasure_on(who="curse"),))
    game = game_with(other=[curse])
    game.pool = ManaPool()
    assert game.combat() is False and game.treasures == []
    game = game_with(creatures=[BEAR], other=[curse])
    game.pool = ManaPool()
    game.combat()
    assert game.treasures == [ANY]


def test_an_aura_goes_on_an_attacker_and_falls_off_with_it():
    impetus = Card("Shiny Impetus", 3, 0, 3, ENCHANTMENT, cost=parse("{2}{R}"),
                   types=frozenset({"enchantment"}), enchants="creature",
                   combat=(treasure_on(who="enchanted"),))
    elf = creature("Llanowar Elves", 1, 1, mana=(ManaAbility(FLAT, {"G": 1}),))
    game = game_with(hand=[impetus])
    assert not game.can_cast(impetus, ManaPool(R=3))
    game = game_with(creatures=[elf, BEAR], hand=[impetus])
    game.cast(impetus, ManaPool(R=3))
    assert named(game, "attached") == {"Shiny Impetus": "Bear"}
    game.sacrifice(BEAR)
    assert named(game, "attached") == {} and impetus in game.graveyard


def test_living_weapon_makes_a_germ_that_attacks_next_turn():
    bitterthorn = Card("Bitterthorn, Nissa's Animus", 3, 0, 3, ARTIFACT, cost=parse("{3}"),
                       types=frozenset({"artifact"}), equip=parse("{3}"), germ=True,
                       combat=(CombatTrigger(event="attacks", who="equipped", search=True),))
    game = game_with()
    game.cast(bitterthorn, ManaPool(colorless=3))
    assert [each.name for each in game.creatures] == [GERM]
    assert named(game, "attached") == {bitterthorn.name: GERM}
    assert game.attackers() == []
    game.begin_turn()
    game.pool = ManaPool()
    game.combat()
    assert len(game.lands) == 1
    game.sacrifice(game.creatures[0])
    assert game.creatures == [] and all(each.name != GERM for each in game.graveyard)


def test_paradise_mantle_makes_mana_from_the_next_turn():
    mantle = Card("Paradise Mantle", 0, 0, 0, ARTIFACT, types=frozenset({"artifact"}),
                  equip=parse("{1}"), mana_abilities=(ManaAbility(GRANT, {ANY: 1},
                                                                  subtype="equipped"),))
    game = game_with(creatures=[BEAR], other=[mantle])
    game.equip(mantle, ManaPool(colorless=1))
    assert named(game, "attached") == {"Paradise Mantle": "Bear"}
    game.begin_turn()
    assert game.open_pool().by_color() == {ANY: 1}
    assert game.attackers() == []


def test_mana_a_trigger_adds_in_combat_is_lost():
    cobra = replace(creature("Lotus Cobra", 2, 1, types=("snake",), cost="{1}{G}"),
                    triggers=(Trigger(LANDFALL, mana=1, mana_color=ANY),))
    game = game_with(creatures=[cobra, BEAR], other=[SWORD])
    marks(game, attached={SWORD.name: "Bear"})
    game.pool = ManaPool()
    game.combat()
    assert len(game.lands) == 1 and game.pool.total == 0


def test_the_agent_equips_with_spare_mana_and_attacks():
    game = game_with(creatures=[BEAR], other=[SWORD], lands=[MOUNTAIN, MOUNTAIN])
    game.library = [FOREST] * 3 + [creature(f"Filler {n}") for n in range(20)]
    agent.take_turn(game)
    assert named(game, "attached") == {SWORD.name: "Bear"}
    assert len(game.lands) == 3 and "Combat: Bear attack" in game.log


def test_a_saved_game_keeps_what_is_attached():
    game = game_with(creatures=[BEAR], other=[SWORD])
    marks(game, attached={SWORD.name: "Bear"})
    game.combat_done = True
    game.pool = ManaPool(R=1)
    game.pool.treasure_bonus = 1
    loaded = serial.load_game(serial.dump_game(game), game.deck)
    assert named(loaded, "attached") == named(game, "attached") and loaded.combat_done
    assert loaded.pool.treasure_bonus == 1
    assert card_fields(loaded.other_permanents[0])["combat"] == card_fields(SWORD)["combat"]


def test_advancing_into_combat_fights_it():
    game = game_with(creatures=[LANNERY])
    game.phase = actions.MAIN1
    game.pool = ManaPool()
    actions.apply(game, actions.AdvancePhase())
    assert game.phase == actions.COMBAT and game.treasures == [ANY]


# --- the adapter and the deck page --------------------------------------------------


@pytest.fixture
def combat_deck(db):
    owner = get_user_model().objects.create_user(email="r22@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, cost, power="", toughness="", keywords=()):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name.split(" // ")[0],
            search_name=name.lower(), type_line=type_line, oracle_text=text,
            produced_mana=[], mana_cost=cost, power=power, toughness=toughness,
            keywords=list(keywords))
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return found

    sword = made("Sword of the Animist", "Legendary Artifact — Equipment",
                 "Equipped creature gets +1/+1.\nWhenever equipped creature attacks, you may "
                 "search your library for a basic land card, put it onto the battlefield tapped, "
                 "then shuffle.\nEquip {2}", "{2}", keywords=["Equip"])
    goldspan = made("Goldspan Dragon", "Creature — Dragon",
                    "Flying, haste\nWhenever this creature attacks or becomes the target of a "
                    "spell, create a Treasure token.\nTreasures you control have \"{T}, "
                    "Sacrifice this artifact: Add two mana of any one color.\"", "{3}{R}{R}",
                    "4", "4", keywords=["Flying", "Haste"])
    ragavan = made("Ragavan, Nimble Pilferer", "Legendary Creature — Monkey Pirate",
                   "Whenever Ragavan deals combat damage to a player, create a Treasure token "
                   "and exile the top card of that player's library. Until end of turn, you may "
                   "cast that card.\nDash {1}{R}", "{R}", "2", "1", keywords=["Dash"])
    curse = made("Curse of Opulence", "Enchantment — Aura Curse",
                 "Enchant player\nWhenever enchanted player is attacked, create a Gold token. "
                 "Each opponent attacking that player does the same.", "{R}")
    deck = Deck.objects.create(owner=owner, name="Combat")
    for found in (sword, goldspan, ragavan, curse):
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, sword=sword, goldspan=goldspan,
                           ragavan=ragavan, curse=curse)


def reading_of(deck, oracle_card):
    return next(each for each in adapter.readings(deck) if each.oracle_card.pk == oracle_card.pk)


def assumed(reading):
    return " ".join(gap.text for gap in reading.gaps if gap.field == "assumed_trigger")


def test_every_card_states_its_assumption_beside_it(combat_deck):
    found = {name: reading_of(combat_deck.deck, getattr(combat_deck, name))
             for name in ("sword", "goldspan", "ragavan", "curse")}
    for name, reading in found.items():
        assert not reading.unreadable, name
        grid = deck_cards.GridCard(reading=reading, open=False, answered=False)
        assert grid.assumptions, name
    sword = found["sword"]
    assert card_fields(sword.card)["equip"] == parse("{2}")
    assert "is equipped, with mana left" in assumed(sword)
    assert "basic land" in sword.triggers
    goldspan = found["goldspan"]
    assert goldspan.card.haste and card_fields(goldspan.card)["treasure_bonus"] == 1
    assert "at once with haste" in assumed(goldspan)
    assert "add two mana" in assumed(goldspan)
    ragavan = found["ragavan"]
    assert "against opponents with many blockers this is optimistic" in assumed(ragavan)
    assert "is not played" in assumed(ragavan)
    assert "assuming you attack the enchanted player" in assumed(found["curse"])


def test_the_deck_page_shows_the_combat_assumption(combat_deck, client):
    client.force_login(combat_deck.owner)
    page = client.get(combat_deck.deck.get_absolute_url()).content.decode()
    assert "Assumed:" in page
    assert "a goldfish has no blockers" in page


def test_the_playtest_can_post_equip_and_combat():
    from playtest.forms import ActionForm
    from playtest.views import ACTION_NAMES
    assert {"equip", "combat"} <= set(ACTION_NAMES)
    form = ActionForm({"kind": "equip", "zone": "other_permanents", "index": 0, "target": 1})
    assert form.is_valid()
    assert form.action() == actions.Equip(zone=actions.OTHER, index=0, target=1)


# --- connive in combat (card model phase 5, engine v32) ------------------------------


def test_a_creature_that_connives_as_it_attacks():
    """Ultron, Unlimited: it draws, discards the weakest card and grows when
    that was no land; the card drawn is cast in a second main phase."""
    ultron = creature("Ultron", 4, 4, mv=6, combat=(
        CombatTrigger(event="attacks", who="self", connive=1),))
    huge = creature("Huge", 9, 9, mv=9, cost="{7}{R}{R}")
    cheap = creature("Cheap", 1, 1, mv=1, cost="{R}")
    game = game_with(creatures=[ultron], hand=[huge], lands=[MOUNTAIN])
    game.library = [cheap] + [creature(f"Filler {n}", mv=9, cost="{7}{R}{R}")
                              for n in range(20)]
    agent.take_turn(game)
    assert huge in game.graveyard and game.plus(ultron) == 1
    assert any(each.name == "Cheap" for each in game.creatures)
    assert "  -> Ultron: connives" in game.log


def test_a_connive_needs_an_attacker():
    ultron = creature("Ultron", combat=(CombatTrigger(event="attacks", who="self", connive=1),))
    game = game_with(creatures=[ultron])
    marks(game, arrived=["Ultron"])
    game.pool = ManaPool()
    game.combat()
    assert game.graveyard == [] and game.plus(ultron) == 0
