"""P19 R17 (engine version 21): mana only some spells may spend.

Cavern of Souls, Ancient Ziggurat, Plaza of Heroes, Eldrazi Temple, Power
Depot and the rest pay only for the spells they name. The pool keeps that
mana apart and spends it first on a spell that may use it; what such a land
makes instead ({C}) is spent last, because spending it gives the restricted
mana up. "The chosen type" is the deck's most common creature type - an
assumption the deck page states beside the card, until an annotation names
another.
"""

import random
import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from cards import profiles
from cards.models import OracleCard
from cards.profiles import _mana_production, _spend_only
from decks.models import Deck, DeckCard
from playtest.views import restricted_mana
from simulation import actions
from simulation.cards import (
    CREATURE,
    FLAT,
    LAND,
    SORCERY,
    Card,
    DeckDefinition,
    ManaAbility,
    SpellFilter,
    TappedUnless,
)
from simulation.game import Game
from simulation.mana import ManaPool, RestrictedMana, may_spend
from simulation.manacost import parse
from simulation.serial import dump_game, load_game
from simulations import deck_cards, gaps
from simulations.engine import adapter
from simulations.forms import AnnotationForm
from simulations.models import CardAnnotation

CREATURES = (SpellFilter(types=frozenset({"creature"})),)
ELVES = (SpellFilter(types=frozenset({"creature"}), subtypes=frozenset({"elf"})),)


def restricted(made, spend_only=CREATURES, otherwise=None, source="Source"):
    return RestrictedMana(dict(made), spend_only, dict(otherwise or {}), source)


ELF = Card("Elvish Visionary", 2, 0, 1, CREATURE, cost=parse("{1}{G}"),
           creature_types=frozenset({"elf"}), types=frozenset({"creature"}))
TWO_GREEN_ELF = Card("Elvish Archdruid", 3, 0, 1, CREATURE, cost=parse("{1}{G}{G}"),
                     creature_types=frozenset({"elf"}), types=frozenset({"creature"}))
BEAR = Card("Grizzly Bears", 2, 0, 2, CREATURE, cost=parse("{1}{G}"),
            creature_types=frozenset({"bear"}), types=frozenset({"creature"}))
GROWTH = Card("Explosive Vegetation", 4, 0, 2, SORCERY, cost=parse("{2}{G}{G}"),
              types=frozenset({"sorcery"}))


# --- the pool ---------------------------------------------------------------------


def test_a_matching_spell_spends_the_restricted_mana_first():
    pool = ManaPool(G=2)
    pool.restricted = [restricted({"WUBRG": 1}, ELVES, {"C": 1}, "Cavern of Souls")]
    payment = pool.pay_cost(ELF.mana_cost, spell=ELF)
    assert payment.spent == {"WUBRG": 1, "G": 1}
    # One Forest left for something else, and the Cavern is gone.
    assert pool.by_color() == {"G": 1} and not pool.restricted


def test_another_spell_spends_what_the_source_makes_instead_last():
    pool = ManaPool(G=2)
    pool.restricted = [restricted({"WUBRG": 1}, ELVES, {"C": 1})]
    # The two Forests pay for the Bear; the Cavern stays open for an Elf.
    assert pool.pay_cost(BEAR.mana_cost, spell=BEAR) is not None
    assert pool.restricted and pool.restricted[0].open
    # The sorcery needs the Cavern's {C}: that settles it.
    pool = ManaPool(G=2)
    pool.restricted = [restricted({"WUBRG": 1}, ELVES, {"C": 1})]
    assert pool.can_pay_cost(parse("{2}{G}"), spell=GROWTH)
    assert pool.pay_cost(parse("{2}{G}"), spell=GROWTH).spent == {"G": 2, "C": 1}
    assert not pool.restricted


def test_nothing_else_may_spend_ancient_ziggurat_s_mana():
    pool = ManaPool(G=1)
    pool.restricted = [restricted({"WUBRG": 1})]
    assert not pool.can_pay_cost(parse("{1}{G}"), spell=GROWTH)
    assert not pool.can_pay_cost(parse("{1}{G}"))     # an ability's cost
    assert pool.can_pay_cost(BEAR.mana_cost, spell=BEAR)


def test_an_ability_never_spends_restricted_mana_but_may_take_the_other():
    pool = ManaPool()
    pool.restricted = [restricted({"WUBRG": 1}, ELVES, {"C": 1})]
    assert pool.pay_cost(parse("{1}")).spent == {"C": 1}


def test_eldrazi_temple_makes_two_for_an_eldrazi_one_otherwise():
    eldrazi = Card("Thought-Knot Seer", 4, 0, 3, CREATURE, cost=parse("{3}{C}"),
                   creature_types=frozenset({"eldrazi"}), types=frozenset({"creature"}))
    temple = (SpellFilter(subtypes=frozenset({"eldrazi"}), colorless=True),)
    pool = ManaPool(C=1, G=1)
    pool.restricted = [restricted({"C": 2}, temple, {"C": 1})]
    assert pool.can_pay_cost(eldrazi.mana_cost, spell=eldrazi)
    pool = ManaPool(C=1, G=1)
    pool.restricted = [restricted({"C": 2}, temple, {"C": 1})]
    assert not pool.can_pay_cost(GROWTH.mana_cost, spell=GROWTH)
    assert pool.can_pay_cost(parse("{2}{G}"), spell=GROWTH)


def test_a_choice_taken_for_the_wrong_pip_is_not_the_end():
    # The Cavern's choice is first offered the white pip; only the merged
    # search sees that the Plains pays white and the Cavern blue.
    spell = Card("Elf Knight", 2, 0, 0, CREATURE, cost=parse("{W}{U}"),
                 creature_types=frozenset({"elf"}), types=frozenset({"creature"}))
    pool = ManaPool(W=1)
    pool.restricted = [restricted({"WU": 1}, ELVES)]
    assert pool.pay_cost(spell.mana_cost, spell=spell) is not None
    assert pool.total == 0 and not pool.restricted


@pytest.mark.parametrize("wanted, spell, expected", [
    (SpellFilter(legendary=True), Card("Lathril", 3, 0, 0, CREATURE, legendary=True), True),
    (SpellFilter(legendary=True), BEAR, False),
    (SpellFilter(colorless=True), Card("Ulamog", 10, 0, 10, CREATURE,
                                      types=frozenset({"creature"})), True),
    (SpellFilter(colorless=True), Card("Elf", 1, 0, 0, CREATURE, types=frozenset({"creature"}),
                                       colors=frozenset({"G"})), False),
    (SpellFilter(multicolored=True), Card("Kenrith", 5, 0, 4, CREATURE, cost=parse("{4}{W}"),
                                          colors=frozenset({"W", "G"}),
                                          types=frozenset({"creature"})), True),
    (SpellFilter(noncreature=True), GROWTH, True),
    (SpellFilter(noncreature=True), BEAR, False),
    (SpellFilter(subtypes=frozenset({"aura", "equipment"})),
     Card("Sword", 3, 0, 3, "artifact", types=frozenset({"artifact"}),
          printed_subtypes=frozenset({"equipment"})), True),
    (SpellFilter(types=frozenset({"artifact"})), Card("Sol Ring", 1, 0, 1, "rock"), True),
])
def test_what_a_filter_lets_pay(wanted, spell, expected):
    assert may_spend((wanted,), spell) is expected


# --- the game -----------------------------------------------------------------------


def basic(name, subtype, color):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset({subtype}), basic=True,
                mana_abilities=(ManaAbility(FLAT, {color: 1}),))


FOREST = basic("Forest", "forest", "G")
CAVERN = Card("Cavern of Souls", 0, 0, 0, LAND, mana_abilities=(
    ManaAbility(FLAT, {"WUBRG": 1}, spend_only=ELVES), ManaAbility(FLAT, {"C": 1})))
SHRINE = Card("Shrine of the Forsaken Gods", 0, 0, 0, LAND, mana_abilities=(
    ManaAbility(FLAT, {"C": 2}, only_if=TappedUnless("lands", count=7),
                spend_only=(SpellFilter(colorless=True),)),
    ManaAbility(FLAT, {"C": 1})))
PLAZA = Card("Plaza of Heroes", 0, 0, 0, LAND, mana_abilities=(
    ManaAbility(FLAT, {"WUBRG": 1}, spend_only=(SpellFilter(legendary=True),)),
    ManaAbility(FLAT, {"C": 1})))


def game_with(lands, hand=(), commander=None):
    game = Game(random.Random(1), deck=DeckDefinition("R17", commander, (FOREST,)))
    game.library, game.lands, game.hand = [], list(lands), list(hand)
    game.tapped_lands, game.turn = 0, 3
    actions.OpenMainPhase().run(game, None)
    return game


def test_the_cavern_opens_as_restricted_mana_and_counts_as_mana():
    game = game_with([CAVERN, FOREST, FOREST], hand=[TWO_GREEN_ELF, GROWTH])
    assert game.pool.total == 2 and game.pool.restricted_total == 1
    assert game.mana_available == 3
    assert game.can_cast(TWO_GREEN_ELF, game.pool)
    game.cast(TWO_GREEN_ELF, game.pool)
    assert game.pool.total == 0 and not game.pool.restricted


def test_a_sorcery_cannot_have_the_cavern_s_colour():
    game = game_with([CAVERN, FOREST], hand=[GROWTH])
    assert not game.can_cast(Card("Lay of the Land", 1, 0, 0, SORCERY, cost=parse("{G}{G}"),
                                  types=frozenset({"sorcery"})), game.pool)
    assert game.can_cast(BEAR, game.pool)


def test_shrine_of_the_forsaken_gods_needs_seven_lands():
    game = game_with([SHRINE, FOREST])
    assert game.pool.by_color() == {"G": 1, "C": 1} and not game.pool.restricted
    game = game_with([SHRINE, *[FOREST] * 6])
    assert game.pool.restricted[0].made == {"C": 2}
    assert game.pool.restricted[0].otherwise == {"C": 1}


def test_plaza_of_heroes_pays_for_the_commander():
    lathril = Card("Lathril, Blade of the Elves", 4, 0, 2, CREATURE, cost=parse("{2}{B}{G}"),
                   legendary=True, creature_types=frozenset({"elf"}),
                   types=frozenset({"creature"}))
    black_bear = Card("Black Bear", 4, 0, 2, CREATURE, cost=parse("{2}{B}{G}"),
                      types=frozenset({"creature"}))
    game = game_with([FOREST] * 4, commander=lathril)
    assert not game.can_cast_commander(game.pool)    # no black without the Plaza
    game = game_with([PLAZA, FOREST, FOREST, FOREST], commander=lathril, hand=[black_bear])
    assert not game.can_cast(black_bear, game.pool)  # not legendary
    assert game.can_cast_commander(game.pool)
    game.cast_commander(game.pool)
    assert game.pool.total == 0 and not game.pool.restricted


def test_a_stored_game_keeps_its_restricted_mana():
    game = game_with([CAVERN, FOREST])
    loaded = load_game(dump_game(game), game.deck)
    assert loaded.pool.restricted == game.pool.restricted


# --- the reader ---------------------------------------------------------------------


def card(text, name="Test Card", type_line="Land", produced=("C",)):
    return OracleCard(name=name, front_name=name, oracle_text=text, type_line=type_line,
                      produced_mana=list(produced))


def flags(**set_):
    wanted = {"types": [], "subtypes": [], "legendary": False, "colorless": False,
              "multicolored": False, "noncreature": False, "chosen_type": False}
    wanted.update(set_)
    return wanted


@pytest.mark.parametrize("text, expected", [
    ("As this land enters, choose a creature type.\n{T}: Add one mana of any color. Spend "
     "this mana only to cast a creature spell of the chosen type, and that spell can't be "
     "countered.", [flags(types=["creature"], chosen_type=True)]),
    ("{T}: Add {B}{B}{B}. Spend this mana only to cast Vampire, Cleric, and/or Demon spells.",
     [flags(subtypes=["vampire"]), flags(subtypes=["cleric"]), flags(subtypes=["demon"])]),
    ("{T}: Add {U}. Spend this mana only to cast an instant or sorcery spell.",
     [flags(types=["instant"]), flags(types=["sorcery"])]),
    ("{T}: Add {C}{C}. Spend this mana only to cast colorless Eldrazi spells or activate "
     "abilities of colorless Eldrazi.", [flags(subtypes=["eldrazi"], colorless=True)]),
    ("{T}: Add one mana of any color. Spend this mana only to cast a Dragon creature spell.",
     [flags(types=["creature"], subtypes=["dragon"])]),
    ("{T}: Add {W}{W}. Spend this mana only to cast Aura and/or Equipment spells.",
     [flags(subtypes=["aura"]), flags(subtypes=["equipment"])]),
    ("{T}: Add one mana of any color. Spend this mana only to cast an instant or sorcery "
     "spell or a kicked spell.", [flags(types=["instant"]), flags(types=["sorcery"])]),
])
def test_the_spells_it_pays_for_are_read(text, expected):
    assert _spend_only(text, card(text)) == expected


@pytest.mark.parametrize("text", [
    "{T}: Add {G}{G}. Spend this mana only to cast kicked spells.",
    "{T}: Add {C}. Spend this mana only to cast spells from your graveyard.",
    "{T}: Add {C}. Spend this mana only on spells and abilities that put tokens onto the "
    "battlefield.",
    "{T}: Add {C}. Spend this mana only to cast creature spells with no abilities.",
    # "The chosen type" from a list, not "a creature type": not this one.
    "As this land enters, choose Elf or Goblin.\n{T}: Add {C}. Spend this mana only to cast "
    "a spell of the chosen type.",
])
def test_what_it_cannot_tell_apart_is_not_read(text):
    assert _spend_only(text, card(text)) is None


def test_cavern_of_souls_is_read_whole():
    text = ("As this land enters, choose a creature type.\n{T}: Add {C}.\n{T}: Add one mana "
            "of any color. Spend this mana only to cast a creature spell of the chosen type, "
            "and that spell can't be countered.")
    reading = _mana_production(card(text, "Cavern of Souls", produced="WUBRGC"))
    assert not reading.notes
    assert reading.amount == 1 and reading.produces is None
    assert reading.spend_only == {
        "spells": [flags(types=["creature"], chosen_type=True)],
        "otherwise": {"amount": 1, "produces": {"C": 1}, "activation": 0}}


def test_shrine_carries_both_its_condition_and_its_spells():
    text = ("{T}: Add {C}.\n{T}: Add {C}{C}. Spend this mana only to cast colorless spells. "
            "Activate only if you control seven or more lands.")
    reading = _mana_production(card(text, "Shrine of the Forsaken Gods"))
    assert reading.amount == 2 and reading.produces == {"C": 2}
    assert reading.condition["if"] == {"kind": "lands", "count": 7}
    assert reading.spend_only["spells"] == [flags(colorless=True)]


def test_an_altar_with_restricted_mana_stays_a_gap():
    text = ("{T}, Sacrifice another creature: Add {B}{B}{B}. Spend this mana only to cast "
            "Vampire, Cleric, and/or Demon spells.")
    reading = _mana_production(card(text, "Master of Dark Rites", "Creature — Vampire",
                                    produced="B"))
    assert reading.sacrifice is None and reading.spend_only is None
    assert reading.notes == ["its mana is restricted to certain spells or moments"]


# --- the adapter --------------------------------------------------------------------


def oracle(type_line):
    return SimpleNamespace(type_line=type_line)


def entry(type_line, quantity=1):
    return SimpleNamespace(oracle_card=oracle(type_line), quantity=quantity)


def test_the_deck_s_creature_type_is_its_most_common():
    entries = [entry("Creature — Elf Druid", 3), entry("Creature — Goblin", 4),
               entry("Land")]
    deck = SimpleNamespace(commander_id=None, commander=None)
    assert adapter.deck_creature_type(entries, deck) == ("goblin", 4)


def test_a_tie_goes_to_the_commander_s_type():
    entries = [entry("Creature — Elf", 3), entry("Creature — Goblin", 4)]
    deck = SimpleNamespace(commander_id=1, commander=oracle("Legendary Creature — Elf Warrior"))
    assert adapter.deck_creature_type(entries, deck) == ("elf", 4)


def test_no_creature_type_means_no_spell_for_the_chosen_type():
    assert adapter.spell_filters([flags(types=["creature"], chosen_type=True)], "") == ()
    assert adapter.spell_filters([flags(types=["creature"], chosen_type=True)], "elf") == ELVES


def test_the_card_page_says_which_spells_its_mana_pays_for():
    assert adapter._ability_text(CAVERN.mana_abilities[0]) == (
        "1 W/U/B/R/G, only for Elf creature spells")
    assert adapter.spells_text((SpellFilter(legendary=True),
                                SpellFilter(subtypes=frozenset({"eldrazi"}), colorless=True))) == (
        "legendary spells or colourless Eldrazi spells")


@pytest.fixture
def elf_deck(db):
    owner = get_user_model().objects.create_user(email="r17@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, produced=(), cost=""):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
            type_line=type_line, oracle_text=text, produced_mana=list(produced),
            mana_cost=cost)
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return found

    cavern = made("Cavern of Souls", "Land",
                  "As this land enters, choose a creature type.\n{T}: Add {C}.\n{T}: Add one "
                  "mana of any color. Spend this mana only to cast a creature spell of the "
                  "chosen type, and that spell can't be countered.", "WUBRGC")
    elf = made("Elvish Visionary", "Creature — Elf Shaman", "When this creature enters, draw a "
               "card.", cost="{1}{G}")
    goblin = made("Goblin Guide", "Creature — Goblin Scout", "Haste", cost="{R}")
    deck = Deck.objects.create(owner=owner, name="Elves")
    DeckCard.objects.create(deck=deck, oracle_card=cavern)
    DeckCard.objects.create(deck=deck, oracle_card=elf, quantity=3)
    DeckCard.objects.create(deck=deck, oracle_card=goblin, quantity=2)
    return SimpleNamespace(deck=deck, owner=owner, cavern=cavern)


def test_the_cavern_names_the_deck_s_type_as_a_stated_assumption(elf_deck):
    reading = next(each for each in adapter.readings(elf_deck.deck)
                   if each.oracle_card.pk == elf_deck.cavern.pk)
    assert reading.card.mana_abilities[0].spend_only[0].subtypes == frozenset({"elf"})
    assumed = [gap for gap in reading.gaps if gap.field == "assumed_type"]
    assert [gap.text for gap in assumed] == [
        "names Elf as its creature type, the most common one in this deck (3 cards)"]
    assert gaps.kind_of("assumed_type") == gaps.JUDGEMENT
    assert not reading.unreadable and reading.names_type
    grid = deck_cards.GridCard(reading=reading, open=False, answered=False)
    assert grid.assumptions == [assumed[0].text]


def test_the_deck_page_shows_the_assumption_beside_the_cavern(elf_deck, client):
    client.force_login(elf_deck.owner)
    page = client.get(elf_deck.deck.get_absolute_url()).content.decode()
    assert "Assumed:" in page
    assert "names Elf as its creature type, the most common one in this deck (3 cards)" in page


def test_an_annotation_names_another_type_and_the_assumption_goes(elf_deck):
    CardAnnotation.objects.create(owner=elf_deck.owner, deck=elf_deck.deck,
                                  oracle_card=elf_deck.cavern,
                                  overrides={"chosen_type": "goblin"})
    reading = next(each for each in adapter.readings(elf_deck.deck)
                   if each.oracle_card.pk == elf_deck.cavern.pk)
    assert reading.card.mana_abilities[0].spend_only[0].subtypes == frozenset({"goblin"})
    assert not [gap for gap in reading.gaps if gap.field == "assumed_type"]


@pytest.mark.parametrize("typed, valid, stored", [
    ("Elf", True, "elf"), ("  Time  ", True, "time"), ("", True, ""),
    ("Elf<script>", False, None), ("Elf Druid", False, None),
])
def test_the_type_box_takes_one_creature_type(typed, valid, stored):
    form = AnnotationForm(data={"chosen_type": typed})
    form.is_valid()
    assert ("chosen_type" not in form.errors) is valid
    if valid:
        assert form.cleaned_data["chosen_type"] == stored


def test_the_playtest_board_lists_restricted_mana():
    pool = ManaPool()
    pool.restricted = [restricted({"WUBRG": 1}, ELVES, {"C": 1}, "Cavern of Souls")]
    assert restricted_mana(pool) == [
        "Cavern of Souls: 1 W/U/B/R/G for Elf creature spells, or 1 C"]
    assert restricted_mana(None) == []
