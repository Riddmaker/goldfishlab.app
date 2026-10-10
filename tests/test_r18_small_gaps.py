"""P19 R18 (engine version 22): small readings.

"N mana in any combination of colours"; the colour a card chooses as it
enters - the one the deck's costs ask for most, an assumption the deck page
states beside the card; Three Tree City's chosen creature type; "a creature
with power 4 or greater" by printed power; Nimbus Maze's two conditions;
Rite of Flame and Cabal Ritual counting the graveyard. And hybrid symbols are
no longer a gap: the payer has settled them since engine version 5.
"""

import random
import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from cards import profiles
from cards.models import DerivedProfile, OracleCard
from cards.profiles import _counting_rule, _extra_rule, _mana_production
from decks.models import Deck, DeckCard
from simulation.cards import (
    CREATURE,
    FLAT,
    LAND,
    RITUAL,
    Card,
    DeckDefinition,
    ManaAbility,
    SpellFilter,
    TappedUnless,
)
from simulation.game import Game
from simulation.mana import may_spend
from simulation.manacost import parse
from simulations import deck_cards, gaps
from simulations.engine import adapter
from simulations.forms import AnnotationForm
from simulations.models import CardAnnotation

Kind = DerivedProfile.Kind


def card(text, name="Test Card", type_line="Artifact", produced=("W", "U", "B", "R", "G")):
    return OracleCard(name=name, front_name=name, oracle_text=text, type_line=type_line,
                      produced_mana=list(produced))


# --- the reader -----------------------------------------------------------------


def test_two_mana_in_any_combination_of_colours_is_read():
    reading = _mana_production(card("{T}: Add two mana in any combination of {U}, {B}, "
                                     "and/or {R}.\n{3}, {T}: Draw two cards, then discard a "
                                     "card.", produced=("U", "B", "R")))
    assert (reading.amount, reading.produces, reading.notes) == (2, None, [])


def test_gwenna_s_two_pay_for_creature_spells_only():
    reading = _mana_production(card(
        "{T}: Add two mana in any combination of colors. Spend this mana only to cast "
        "creature spells or activate abilities of creature sources.",
        type_line="Legendary Creature — Elf Druid Scout"))
    assert reading.amount == 2 and not reading.notes
    assert reading.spend_only["spells"][0]["types"] == ["creature"]


def test_the_chosen_colour_is_read_with_the_choice():
    reading = _mana_production(card("As this artifact enters, choose a color.\nCreatures you "
                                    "control of the chosen color get +1/+0.\n{T}: Add one "
                                    "mana of the chosen color."))
    assert (reading.amount, reading.chosen_color, reading.notes) == (1, True, [])


def test_a_chosen_colour_nothing_chooses_is_not_read():
    reading = _mana_production(card("{T}: Add one mana of the chosen color."))
    assert reading.amount is None and reading.notes


def test_throne_of_eldraine_pays_for_monocoloured_spells_of_its_colour():
    reading = _mana_production(card(
        "As Throne of Eldraine enters, choose a color.\n{T}: Add four mana of the chosen "
        "color. Spend this mana only to cast monocolored spells of that color.\n{3}, {T}: "
        "Draw two cards. Spend only mana of the chosen color to activate this ability.",
        name="Throne of Eldraine", type_line="Legendary Artifact"))
    assert (reading.amount, reading.chosen_color, reading.notes) == (4, True, [])
    assert reading.spend_only["spells"] == [{
        "types": [], "subtypes": [], "legendary": False, "colorless": False,
        "multicolored": False, "noncreature": False, "chosen_type": False,
        "chosen_color": True}]


def test_mana_that_cannot_pay_generic_costs_stays_a_gap():
    reading = _mana_production(card("{T}: Add {W}{U}{B}{R}{G}. This mana can't be spent to "
                                    "pay generic mana costs.", type_line="Creature — Elk"))
    assert reading.amount is None
    assert reading.notes == ["its mana is restricted to certain spells or moments"]


def test_a_creature_with_power_four_guards_the_big_ability():
    reading = _mana_production(card(
        "{T}: Add {G}.\nFerocious — {T}: Add {G}{G}{G}{G}. Activate only if you control a "
        "creature with power 4 or greater.", type_line="Creature — Snake Druid",
        produced=("G",)))
    assert (reading.amount, reading.produces) == (4, {"G": 4})
    assert reading.condition == {"if": {"kind": "power", "count": 4},
                                 "otherwise": {"amount": 1, "produces": {"G": 1},
                                               "activation": 0}}


def test_ilysian_caryatid_makes_two_beside_a_big_creature():
    reading = _mana_production(card(
        "{T}: Add one mana of any color. If you control a creature with power 4 or greater, "
        "add two mana of any one color instead.", type_line="Creature — Plant"))
    assert reading.amount == 2 and not reading.notes
    assert reading.condition["if"] == {"kind": "power", "count": 4}
    assert reading.condition["otherwise"]["amount"] == 1


def test_nimbus_maze_holds_both_of_its_conditions():
    reading = _mana_production(card(
        "{T}: Add {C}.\n{T}: Add {W}. Activate only if you control an Island.\n{T}: Add {U}. "
        "Activate only if you control a Plains.", type_line="Land", produced=("C", "W", "U")))
    assert not reading.notes
    assert (reading.amount, reading.produces) == (1, {"W": 1})
    assert reading.condition == {
        "if": {"kind": "control_type", "types": ["island"]},
        "otherwise": {"amount": 1, "produces": {"C": 1}, "activation": 0},
        "also": [{"if": {"kind": "control_type", "types": ["plains"]}, "amount": 1,
                  "produces": {"U": 1}, "activation": 0}]}


def test_rite_of_flame_counts_its_own_name():
    assert _extra_rule("Add {R}{R}, then add {R} for each card named Rite of Flame in each "
                       "graveyard.", Kind.RITUAL) == {
        "rule": "ritual", "subtype": "named:Rite of Flame", "color": "R", "activation": 0}


def test_cabal_ritual_s_threshold_is_read():
    assert _extra_rule("Add {B}{B}{B}.\nThreshold — Add {B}{B}{B}{B}{B} instead if there "
                       "are seven or more cards in your graveyard.", Kind.RITUAL) == {
        "rule": "ritual", "subtype": "threshold:7:5", "color": "B", "activation": 0}


def test_three_tree_city_counts_the_chosen_type():
    assert _counting_rule(
        "As Three Tree City enters, choose a creature type.\n{T}: Add {C}.\n{2}, {T}: Choose "
        "a color. Add an amount of mana of that color equal to the number of creatures you "
        "control of the chosen type.") == {
        "rule": "counts", "subtype": "chosen_type", "color": "", "activation": 2}


@pytest.mark.django_db
def test_a_hybrid_cost_is_no_reason_to_review():
    found = OracleCard.objects.create(
        oracle_id=uuid.uuid4(), name="Hybrid Bear", front_name="Hybrid Bear",
        search_name="hybrid bear", type_line="Creature — Bear", oracle_text="",
        mana_cost="{1}{B/G}{B/G}")
    profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
    found.refresh_from_db()
    assert found.profile.review_reasons == []


# --- the engine -------------------------------------------------------------------


def creature(name, power, mana=()):
    return Card(name, 2, 0, 2, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}),
                power=power, mana_abilities=tuple(mana), creature_types=frozenset({"elf"}),
                colors=frozenset({"G"}))


FANATIC = creature("Fanatic of Rhonas", 1, (
    ManaAbility(FLAT, {"G": 4}, only_if=TappedUnless("power", count=4)),
    ManaAbility(FLAT, {"G": 1})))


def game_with(lands=(), creatures=(), graveyard=(), hand=()):
    game = Game(random.Random(1), deck=DeckDefinition("R18", None, (creature("Filler", 1),)))
    game.lands, game.creatures = list(lands), list(creatures)
    game.graveyard, game.hand = list(graveyard), list(hand)
    game.tapped_lands = game.tapped_rocks = 0
    return game


def test_fanatic_of_rhonas_needs_a_creature_with_power_four():
    alone = game_with(creatures=[FANATIC])
    assert not alone.holds(FANATIC.mana_abilities[0].only_if, FANATIC)
    beside = game_with(creatures=[FANATIC, creature("Craw Wurm", 6)])
    assert beside.holds(FANATIC.mana_abilities[0].only_if, FANATIC)


def ritual(name, gain, counts, color):
    return Card(name, 1, 0, 0, RITUAL, ritual_gain=gain, ritual_color=color,
                ritual_counts=counts, types=frozenset({"sorcery"}))


def test_rite_of_flame_adds_one_for_each_copy_in_the_graveyard():
    rite = ritual("Rite of Flame", 2, "named:Rite of Flame", "R")
    assert game_with().ritual_mana(rite) == ("R", 2)
    assert game_with(graveyard=[rite, rite]).ritual_mana(rite) == ("R", 4)


def test_cabal_ritual_makes_five_with_seven_cards_in_the_graveyard():
    cabal = ritual("Cabal Ritual", 3, "threshold:7:5", "B")
    assert game_with(graveyard=[cabal] * 6).ritual_mana(cabal) == ("B", 3)
    assert game_with(graveyard=[cabal] * 7).ritual_mana(cabal) == ("B", 5)


def test_only_a_spell_of_the_one_colour_may_spend_throne_mana():
    throne = (SpellFilter(only_color="G"),)
    assert may_spend(throne, creature("Llanowar Elves", 1))
    gold = Card("Gold Bear", 2, 0, 0, CREATURE, cost=parse("{B}{G}"),
                types=frozenset({"creature"}), colors=frozenset({"B", "G"}))
    assert not may_spend(throne, gold)


def test_three_tree_city_counts_the_type_in_the_colour_the_hand_wants():
    city = Card("Three Tree City", 0, 0, 0, LAND, mana_abilities=(
        ManaAbility("counts", subtype="elf", activation_generic=2),
        ManaAbility(FLAT, {"C": 1})))
    elves = [creature(f"Elf {n}", 1) for n in range(4)]
    game = game_with(lands=[city], creatures=elves, hand=[creature("Wanted", 1)])
    assert game.board_count(city.mana_abilities[0], city) == (4, "G")


def test_nimbus_maze_takes_the_first_condition_that_holds():
    island = Card("Island", 0, 0, 0, LAND, subtypes=frozenset({"island"}), basic=True,
                  mana_abilities=(ManaAbility(FLAT, {"U": 1}),))
    maze = Card("Nimbus Maze", 0, 0, 0, LAND, mana_abilities=(
        ManaAbility(FLAT, {"W": 1}, only_if=TappedUnless("control_type",
                                                         types=frozenset({"island"}))),
        ManaAbility(FLAT, {"U": 1}, only_if=TappedUnless("control_type",
                                                         types=frozenset({"plains"}))),
        ManaAbility(FLAT, {"C": 1})))
    assert game_with(lands=[maze]).mana().by_color() == {"C": 1}
    assert game_with(lands=[maze, island]).mana().by_color() == {"W": 1, "U": 1}


# --- the adapter ----------------------------------------------------------------


def entry(cost, quantity=1):
    return SimpleNamespace(oracle_card=SimpleNamespace(mana_cost=cost), quantity=quantity)


def test_the_deck_s_colour_is_the_one_its_costs_ask_for_most():
    entries = [entry("{1}{G}{G}", 2), entry("{B}"), entry("{2}{B/G}")]
    deck = SimpleNamespace(commander_id=None, commander=None)
    assert adapter.deck_color(entries, deck, frozenset("BG")) == ("G", 5)


def test_a_tie_goes_to_the_commander_s_first_colour():
    entries = [entry("{G}{G}"), entry("{B}{B}")]
    deck = SimpleNamespace(commander_id=1,
                           commander=SimpleNamespace(mana_cost="{2}", colors=["G", "B"]))
    assert adapter.deck_color(entries, deck, frozenset("BG")) == ("B", 2)


def test_a_colour_outside_the_deck_s_colours_is_never_chosen():
    entries = [entry("{R}{R}{R}"), entry("{G}")]
    deck = SimpleNamespace(commander_id=None, commander=None)
    assert adapter.deck_color(entries, deck, frozenset("G")) == ("G", 1)


def test_the_card_page_says_what_the_power_condition_is():
    assert adapter._ability_text(FANATIC.mana_abilities[0]) == (
        "4 G if you control a creature with power 4 or greater")
    assert adapter.spells_text((SpellFilter(only_color="G"),)) == "monocoloured G spells"


@pytest.fixture
def banner_deck(db):
    owner = get_user_model().objects.create_user(email="r18@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, produced=(), cost=""):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
            type_line=type_line, oracle_text=text, produced_mana=list(produced),
            mana_cost=cost)
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return found

    banner = made("Heraldic Banner", "Artifact",
                  "As this artifact enters, choose a color.\nCreatures you control of the "
                  "chosen color get +1/+0.\n{T}: Add one mana of the chosen color.", "WUBRG",
                  cost="{3}")
    throne = made("Throne of Eldraine", "Legendary Artifact",
                  "As Throne of Eldraine enters, choose a color.\n{T}: Add four mana of the "
                  "chosen color. Spend this mana only to cast monocolored spells of that "
                  "color.", "WUBRG", cost="{5}")
    city = made("Three Tree City", "Legendary Land",
                "As Three Tree City enters, choose a creature type.\n{T}: Add {C}.\n{2}, {T}: "
                "Choose a color. Add an amount of mana of that color equal to the number of "
                "creatures you control of the chosen type.", "WUBRGC")
    elf = made("Elvish Visionary", "Creature — Elf Shaman", "", cost="{1}{G}")
    goblin = made("Goblin Guide", "Creature — Goblin Scout", "Haste", cost="{R}")
    deck = Deck.objects.create(owner=owner, name="Banners")
    for found, quantity in ((banner, 1), (throne, 1), (city, 1), (elf, 3), (goblin, 2)):
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=quantity)
    return SimpleNamespace(deck=deck, owner=owner, banner=banner, throne=throne, city=city)


def reading_of(deck, oracle_card):
    return next(each for each in adapter.readings(deck) if each.oracle_card.pk == oracle_card.pk)


TEXT = "chooses {G}, the colour most mana symbols in this deck's costs ask for (3)"


def test_the_banner_makes_the_deck_s_colour_as_a_stated_assumption(banner_deck):
    reading = reading_of(banner_deck.deck, banner_deck.banner)
    assert reading.card.mana_abilities[0].produces == (("G", 1),)
    assumed = [gap for gap in reading.gaps if gap.field == "assumed_color"]
    assert [gap.text for gap in assumed] == [TEXT]
    assert gaps.kind_of("assumed_color") == gaps.JUDGEMENT
    assert "assumed_color" in gaps.ASSUMPTION_FIELDS
    assert not reading.unreadable and reading.names_color
    grid = deck_cards.GridCard(reading=reading, open=False, answered=False)
    assert grid.assumptions == [TEXT]


def test_the_throne_pays_for_spells_of_that_colour_only(banner_deck):
    ability = reading_of(banner_deck.deck, banner_deck.throne).card.mana_abilities[0]
    assert ability.produces == (("G", 4),)
    assert ability.spend_only == (SpellFilter(only_color="G"),)


def test_three_tree_city_counts_the_deck_s_type_and_says_so(banner_deck):
    reading = reading_of(banner_deck.deck, banner_deck.city)
    assert reading.card.mana_abilities[0].subtype == "elf"
    assert [gap.field for gap in reading.gaps if gap.field.startswith("assumed")] == [
        "assumed_type"]
    assert reading.names_type


def test_the_deck_page_shows_the_colour_beside_the_banner(banner_deck, client):
    client.force_login(banner_deck.owner)
    page = client.get(banner_deck.deck.get_absolute_url()).content.decode()
    assert "Assumed:" in page
    assert "chooses {G}, the colour most mana symbols in this deck&#x27;s costs" in page


def test_an_annotation_chooses_another_colour_and_the_assumption_goes(banner_deck):
    CardAnnotation.objects.create(owner=banner_deck.owner, deck=banner_deck.deck,
                                  oracle_card=banner_deck.banner,
                                  overrides={"chosen_color": "R"})
    reading = reading_of(banner_deck.deck, banner_deck.banner)
    assert reading.card.mana_abilities[0].produces == (("R", 1),)
    assert not [gap for gap in reading.gaps if gap.field == "assumed_color"]


@pytest.mark.parametrize("chosen, valid", [("G", True), ("", True), ("X", False)])
def test_the_colour_box_takes_one_colour(chosen, valid):
    form = AnnotationForm(data={"chosen_color": chosen})
    form.is_valid()
    assert ("chosen_color" not in form.errors) is valid


def test_the_card_editor_asks_for_the_colour_first_only_where_one_is_chosen():
    form = AnnotationForm()
    first, more = form.split(set(), is_land=False, names_color=True)
    assert first[0].name == "chosen_color"
    first, more = form.split(set(), is_land=False)
    assert "chosen_color" not in [bound.name for bound in first + more]
