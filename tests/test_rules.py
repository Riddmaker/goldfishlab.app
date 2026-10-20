"""Card model phase 4, PR 13: the interpreter's atoms and the mana family as blocks.

What these tests hold:

1. **The atoms** - selector, amount, condition - answer as the engine's old
   dialects did: a spell filter, a "wanted" string, the counting strings,
   the kinds `Game.holds` asked.
2. **The mana family round-trips**: the sixteen fields become blocks and come
   back exactly; a card made with fields equals one made with blocks.
3. **The engine reads blocks only**: `game.py`, `mana.py`, `agent.py`,
   `actions.py` and `analysis.py` never import `simulation.legacy` and never
   read a field of the mana family.

The whole catalogue's round trip is `engine_coverage --diff` against the
latest frozen reading; in CI it is the coverage snapshot of the 3,070 cards.
No Django, like the engine.
"""

import ast
import dataclasses
import random
from pathlib import Path

import pytest

from simulation import serial
from simulation.cards import (
    COUNTS,
    CREATURE,
    FLAT,
    LAND,
    MANA_FIELDS,
    ROCK,
    AdditionalCost,
    Card,
    CostReduction,
    DeckDefinition,
    ManaAbility,
    SpellFilter,
    Store,
    TapMana,
    TappedUnless,
)
from simulation.fixtures import chainer
from simulation.game import Game
from simulation.manacost import parse
from simulation.rules import mana
from simulation.rules.amount import Amount, compile_amount
from simulation.rules.condition import KINDS, Condition, check
from simulation.rules.selector import (
    NOTHING,
    Selector,
    compile_selector,
    wanted,
    wanted_of,
)
from tests.engine_builders import game_with

BEAR = Card("Bear", 2, 0, 2, CREATURE, types=frozenset({"creature"}), power=2, toughness=2,
            colors=frozenset({"G"}), creature_types=frozenset({"bear"}))
ELF = Card("Elf", 1, 0, 1, CREATURE, types=frozenset({"creature"}), power=1,
           colors=frozenset({"G"}), creature_types=frozenset({"elf"}), legendary=True)
SWAMP = Card("Swamp", 0, 0, 0, LAND, subtypes=frozenset({"swamp"}), basic=True,
             types=frozenset({"land"}))
RING = Card("Sol Ring", 1, 0, 1, ROCK)


# --- selectors -----------------------------------------------------------------------


@pytest.mark.parametrize("spell_filter, card, expected", [
    (SpellFilter(types=frozenset({"creature"})), BEAR, True),
    (SpellFilter(types=frozenset({"artifact"})), RING, True),
    (SpellFilter(noncreature=True), BEAR, False),
    (SpellFilter(subtypes=frozenset({"elf"})), ELF, True),
    (SpellFilter(legendary=True), BEAR, False),
    (SpellFilter(colorless=True), RING, True),
    (SpellFilter(only_color="G"), ELF, True),
    (SpellFilter(multicolored=True), ELF, False),
])
def test_a_spend_limit_reads_a_spell_as_its_filter_did(spell_filter, card, expected):
    assert compile_selector(mana.spell_selector(spell_filter))(card) is expected
    assert mana.spell_filter(mana.spell_selector(spell_filter)) == spell_filter


@pytest.mark.parametrize("types, wanted_, card, expected", [
    ({"creature"}, "", BEAR, True),
    ({"creature"}, "legendary", BEAR, False),
    ({"creature"}, "legendary", ELF, True),
    ({"creature"}, "G", BEAR, True),
    ({"creature"}, "elf", ELF, True),
    ({"permanent"}, "", RING, True),
    ({"artifact"}, "", RING, True),
    (set(), "", BEAR, False),
])
def test_a_wanted_string_is_one_selector(types, wanted_, card, expected):
    selector = wanted(frozenset(types), wanted_)
    assert compile_selector(selector)(card) is expected
    assert wanted_of(selector) == (frozenset(types), wanted_)


def test_an_empty_type_set_asks_for_nothing():
    assert wanted(frozenset(), "").types == {NOTHING}


def test_alternatives_widen_a_selector():
    swamp = Selector(basic=True, subtypes=frozenset({"swamp"}),
                     alternatives=(Selector(land_type="basic_swamp"),))
    old_swamp = Card("Swamp", 0, 0, 0, LAND, land_type="basic_swamp")
    check_ = compile_selector(swamp)
    assert check_(SWAMP) and check_(old_swamp) and not check_(BEAR)


# --- amounts ---------------------------------------------------------------------------


@pytest.mark.parametrize("what", [
    "creature", "basic:swamp", "graveyard:B", "power", "counters", "power:greatest",
    "toughness:greatest_other", "devotion:G", "devotion", "enchantment", "creature:defender",
    "elf",
])
def test_a_counting_string_is_an_amount_and_back(what):
    assert mana.counted_as(mana.counted(what)) == what


@pytest.mark.parametrize("counts", ["", "creature", "tapped:island", "named:Rite of Flame",
                                    "threshold:7:5"])
def test_a_ritual_counting_string_is_an_amount_and_back(counts):
    assert mana.ritual_counts_of(mana.ritual_counted(counts, 2)) == counts


def test_amounts_count_the_board():
    game = game_with(lands=[SWAMP, SWAMP], creatures=[BEAR, ELF],
                     graveyard=[Card("Gravecrawler", 1, 1, 0, CREATURE,
                                     types=frozenset({"creature"}), cost=parse("{B}"))])
    count = compile_amount
    assert count(mana.counted("creature"))(game, BEAR) == 2
    assert count(mana.counted("basic:swamp"))(game, BEAR) == 2
    assert count(mana.counted("graveyard:B"))(game, BEAR) == 1
    assert count(mana.counted("elf"))(game, BEAR) == 1
    assert count(mana.counted("power:greatest"))(game, BEAR) == 2
    assert count(Amount("threshold", zone="graveyard", n=3, at_least=1, more=5))(game, BEAR) == 5
    named = mana.counted("names:Swamp|Bear", (("C", 7),))
    assert count(named)(game, None) == 0
    game.lands.append(Card("Bear", 0, 0, 0, LAND))
    assert count(named)(game, None) == 7


def test_the_best_devotion_brings_its_colour():
    game = game_with(creatures=[Card("Ghast", 4, 1, 3, CREATURE, cost=parse("{3}{B}"))])
    assert compile_amount(Amount("best_devotion"))(game, None) == (1, "B")


# --- conditions --------------------------------------------------------------------------


def test_every_kind_holds_asks_is_a_condition():
    assert set(KINDS) == {"control_type", "lands", "artifacts", "opponents", "permanent",
                          "turn", "opponent_lands", "life_at_most", "power", "reveal",
                          "pay_life", "tap_fodder"}


@pytest.mark.parametrize("condition, expected", [
    (Condition("control_type", types=frozenset({"swamp"})), True),
    (Condition("lands", count=2), True),
    (Condition("lands", count=3), False),
    (Condition("lands", count=1, other=True), True),
    (Condition("lands", count=1, at_least=False), False),
    (Condition("artifacts", count=1), True),
    (Condition("opponents", count=2), True),
    (Condition("permanent", types=frozenset({"creature"}), legendary=True), True),
    (Condition("turn", count=2), True),
    (Condition("opponent_lands", count=4), False),
    (Condition("life_at_most", count=40), True),
    (Condition("power", count=3), False),
    (Condition("reveal", types=frozenset({"bear"})), True),
    (Condition("pay_life", count=2), True),
    (Condition("nonsense"), False),
])
def test_conditions_ask_the_board(condition, expected):
    game = game_with(lands=[SWAMP, SWAMP], rocks=[Card("Ring", 1, 0, 1, ROCK,
                                                       types=frozenset({"artifact"}))],
                     creatures=[ELF], hand=[BEAR], turn=2)
    assert check(condition)(game, SWAMP) is expected


def test_a_land_entering_counts_itself_only_when_it_is_one_of_them():
    game = game_with(lands=[SWAMP])
    basics = Condition("lands", count=2, basic=True)
    assert check(basics)(game, SWAMP, True)
    assert not check(basics)(game, Card("Battle Land", 0, 0, 0, LAND), True)


def test_a_tapped_unless_asks_as_its_condition():
    unless = TappedUnless("lands", count=1)
    game = game_with(lands=[SWAMP])
    assert game.holds(unless, SWAMP) is check(Condition.of(unless))(game, SWAMP)


# --- the mana family as blocks ---------------------------------------------------------------

#: Every field of the family away from its default.
FAMILY = {
    "mana_abilities": (
        ManaAbility(FLAT, {"B": 1}, only_if=TappedUnless("lands", count=5),
                    spend_only=(SpellFilter(types=frozenset({"creature"})),)),
        ManaAbility(COUNTS, subtype="elf", color="G", activation_generic=1),
        ManaAbility("per_controlled", subtype="swamp", activation_generic=2),
        ManaAbility("type_adding", subtype="swamp"),
        ManaAbility("double_subtype", subtype="swamp", color="B"),
        ManaAbility("extra", {"G": 1}, subtype="enchanted"),
        ManaAbility("multiply", subtype="permanent", times=2),
        ManaAbility("grant", {"WUBRG": 1}, subtype="creature"),
        ManaAbility("filter", {"W": 2}, pays_with="WB"),
        ManaAbility("lands_could_produce", times=3),
        ManaAbility(COUNTS, subtype="names:Urza's Mine|Urza's Tower", produces={"C": 7}),
        ManaAbility(COUNTS, subtype="colors_among"),
    ),
    "ritual_gain": 3,
    "ritual_color": "R",
    "cost_reduction": CostReduction(amount=2, requires_pip=False, color="G"),
    "untaps": False,
    "ritual_counts": "named:Rite of Flame",
    "sacrifice_mana": AdditionalCost(sacrifice=frozenset({"creature"}), sacrifice_filter="goblin",
                                     life=1),
    "sacrifice_mana_amount": 2,
    "sacrifice_mana_color": "R",
    "sacrifice_mana_taps": True,
    "sacrifice_mana_other": True,
    "tap_mana": TapMana(types=frozenset({"creature"}), filter="legendary", mana="WUBRG",
                        amount=2, spend_only=(SpellFilter(legendary=True),), tokens=1),
    "mox": "imprint",
    "mana_mills": 1,
    "store": Store(use="free", mana="R", amount=2, per_use=3, energy=True, start=1,
                   start_from="x", grows="red_spell", empty="hand"),
    "flip": 4,
}


def test_the_family_is_every_field_card_takes():
    assert tuple(mana.FIELDS) == MANA_FIELDS
    assert set(FAMILY) == set(mana.FIELDS)
    assert all(FAMILY[name] != default for name, default in mana.FIELDS.items())


def test_every_field_comes_back_exactly():
    assert mana.decode(mana.encode(FAMILY)) == FAMILY
    assert mana.decode(mana.encode({})) == mana.FIELDS
    assert mana.encode({}) == ()


def test_a_card_made_with_fields_is_a_card_made_with_blocks():
    card = Card("Everything", 3, 1, 2, CREATURE, **FAMILY)
    again = Card("Everything", 3, 1, 2, CREATURE, abilities=mana.encode(FAMILY))
    assert card == again and hash(card) == hash(again)
    assert {name: getattr(card, name) for name in MANA_FIELDS} == FAMILY
    # Changing one field keeps the others.
    changed = dataclasses.replace(card, ritual_gain=5)
    assert changed.ritual_gain == 5 and changed.store == FAMILY["store"]
    assert dataclasses.replace(card, priority=3).abilities == card.abilities


def test_a_stored_card_keeps_the_field_form():
    card = Card("Everything", 3, 1, 2, CREATURE, **FAMILY)
    dumped = serial.dump(card)
    assert "abilities" not in dumped and dumped["store"]["_type"] == "Store"
    assert serial.load(Card, dumped) == card


def test_the_view_holds_what_the_engine_asks():
    view = Card("Everything", 3, 1, 2, CREATURE, **FAMILY).mana
    assert [each.rule for each in view.entries] == [
        each.rule for each in FAMILY["mana_abilities"]]
    assert [each.rule for each in view.sources] == [
        "flat", "counts", "per_controlled", "filter", "lands_could_produce", "counts", "counts"]
    assert view.land_types == ("swamp",)
    assert view.ritual.color == "R" and view.reduction.amount == 2
    assert not view.untaps and view.altar.taps and view.altar.other
    assert view.altar.sacrifice == {"creature"} and view.altar.sacrifice_filter == "goblin"
    assert view.tap_other.tokens == 1 and view.mox == "imprint" and view.mills == 1
    assert view.store.per_use == 3 and view.flip == 4
    assert view.taps_another is False
    flat = view.sources[0]
    assert flat.holds is not None and flat.spends(BEAR) and not flat.spends(RING)


def test_the_reference_deck_reads_as_its_rules():
    by_name = {card.name: card.mana for card in chainer.DECK.library}
    assert by_name["Cabal Coffers"].source("per_controlled").scaling_color == "B"
    assert by_name["Urborg, Tomb of Yawgmoth"].land_types == ("swamp",)
    assert by_name["Crypt Ghast"].of_rule("double_subtype")[0].subtype == "swamp"
    assert by_name["Dark Ritual"].ritual.gain == 3
    assert by_name["Jet Medallion"].reduction.color == "B"


def test_a_ritual_adds_what_its_amount_counts():
    rite = Card("Rite of Flame", 1, 1, 0, "ritual", ritual_gain=2, ritual_color="R",
                ritual_counts="named:Rite of Flame")
    game = Game(random.Random(1), deck=DeckDefinition("R", None, (rite,)))
    game.graveyard = [rite, rite]
    assert game.ritual_mana(rite) == ("R", 4)


# --- the engine reads blocks only ---------------------------------------------------------

ENGINE = ("game.py", "mana.py", "agent.py", "actions.py", "analysis.py")

#: What the engine may not read any more: the family's fields that are not
#: also the name of something in the compiled view, and the old classes.
FORBIDDEN = {"mana_abilities", "ritual_gain", "ritual_color", "ritual_counts", "cost_reduction",
             "sacrifice_mana", "sacrifice_mana_amount", "sacrifice_mana_color",
             "sacrifice_mana_taps", "sacrifice_mana_other", "tap_mana", "mana_mills", "legacy"}
OLD_CLASSES = {"ManaAbility", "SpellFilter", "TapMana", "CostReduction", "Store"}


@pytest.mark.parametrize("module", ENGINE)
def test_the_engine_never_reads_the_old_fields(module):
    path = Path(__file__).resolve().parent.parent / "simulation" / module
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "simulation.legacy", module
            assert not {alias.name for alias in node.names} & OLD_CLASSES, module
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN, f"{module}:{node.lineno} reads .{node.attr}"
            assert node.attr != "ability", f"{module}:{node.lineno} asks .ability()"
        if isinstance(node, ast.Name):
            assert node.id not in OLD_CLASSES, f"{module}:{node.lineno} names {node.id}"
