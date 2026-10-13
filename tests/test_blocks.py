"""Card model phase 0: a card as blocks, and back; stable ids for corrections.

`simulation.blocks` describes the engine's `Card` as abilities made of cost
atoms, events, conditions and effects. Before anything is built on it, it has
to lose nothing: every card the engine reads comes back equal. The committed
coverage snapshot (3,070 cards) and the hand-written reference deck are
checked here; `engine_coverage --blocks` does the same for the whole frozen
catalogue (32,116 cards on 2026-10-13).
"""

import dataclasses

import pytest

from simulation import blocks, serial
from simulation.cards import (
    FLAT,
    LANDER,
    AdditionalCost,
    Card,
    CombatTrigger,
    CostReduction,
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
from simulation.manacost import ManaCost
from simulations import coverage

BASE = Card(name="Test", mv=2, pips=0, generic=2, kind="artifact")

#: One card for each field a block stands for, set to something not default.
EXAMPLES = {
    "mana_abilities": dict(mana_abilities=(
        ManaAbility(FLAT, (("G", 1),), only_if=TappedUnless("lands", count=5)),
        ManaAbility(FLAT, (("C", 1),), activation_generic=1,
                    spend_only=(SpellFilter(types=frozenset({"artifact"})),)))),
    "untaps": dict(untaps=False),
    "mana_mills": dict(mana_mills=1),
    "ritual": dict(ritual_gain=3, ritual_color="R", ritual_counts="creature"),
    "cast_costs": dict(discard_cost=1, additional_costs=(
        AdditionalCost(sacrifice=frozenset({"creature"}), sacrifice_filter="G"),
        AdditionalCost(life=3), AdditionalCost(life_x=True),
        AdditionalCost(mana=ManaCost(generic=2), exile_from_graveyard="creature"))),
    "resolve": dict(draw_on_cast=2, draws_x=True, discard_on_cast=2, put_back_on_cast=1,
                    life_on_cast=2, treasures=2, treasure_mana="R", landers=1),
    "tutor": dict(tutor=TutorSpec(life=3, to_top=True, types=frozenset({"artifact"}),
                                  upkeep="opponent_creatures", max_mv=3)),
    "land_search": dict(land_search=LandSearch(
        when="activate", cost=ManaCost(generic=1), taps=True, life=1, sacrifice=True,
        sacrifices_land=True, land_cost_types=frozenset({"forest"}),
        sacrifice_other=frozenset({"creature"}), discard=1,
        condition="opponent_more_lands", hand=1, types=frozenset({"plains"}))),
    "enters_tapped": dict(enters_tapped=True, tapped_unless=TappedUnless("pay_life", count=2)),
    "tapped_unless_alone": dict(tapped_unless=TappedUnless("turn", count=3)),
    "cost_reduction": dict(cost_reduction=CostReduction(color="G")),
    "cost_reduction_default": dict(cost_reduction=CostReduction()),
    "upkeep": dict(upkeep=UpkeepSpec(draw=1, life=0, life_per_mv=True)),
    "end_step": dict(end_step=EndStepSpec()),
    "skips_draw_step": dict(skips_draw_step=True),
    "exiled_on_cast": dict(exiled_on_cast=True),
    "mox": dict(mox="imprint"),
    "sacrifice_mana": dict(sacrifice_mana=AdditionalCost(sacrifice=frozenset({"creature"}),
                                                         life=1),
                           sacrifice_mana_amount=2, sacrifice_mana_color="B",
                           sacrifice_mana_taps=True, sacrifice_mana_other=True),
    "triggers": dict(triggers=(Trigger("landfall", mana=1, mana_color="any"),
                               Trigger("dies", filter="another", treasures=1,
                                       treasure_mana="C", draw=1, life=1, counter=1),
                               Trigger("upkeep"))),
    "combat": dict(combat=(CombatTrigger("attacks", who="any", types=frozenset({"dragon"}),
                                         treasures=1, treasure_mana="C"),
                           CombatTrigger("damage", mana="RRR", search=True, untapped=True))),
    "tap_mana": dict(tap_mana=TapMana(types=frozenset({"creature"}), filter="legendary",
                                      mana="any", amount=2, tokens=1)),
    "store": dict(store=Store(use="scales", amount=1, start_from="x", empty="hand")),
    "growth": dict(growth=Growth(enters=2, grows=("evolve",), adapt=3,
                                 adapt_cost=ManaCost(generic=3), bonus=1)),
    "flip": dict(flip=5),
    "equip": dict(equip=ManaCost(generic=2)),
    "germ": dict(germ=True),
    "treasure_bonus": dict(treasure_bonus=1),
    "ritual_color_unused": dict(ritual_color="U"),
}


def _round_trips(card: Card) -> None:
    back = blocks.from_blocks(blocks.to_blocks(card))
    assert back == card, [name for name in (f.name for f in dataclasses.fields(Card))
                          if getattr(back, name) != getattr(card, name)]


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_each_structure_comes_back(name):
    _round_trips(dataclasses.replace(BASE, **EXAMPLES[name]))


def test_every_field_is_placed_somewhere():
    """A new `Card` field fails here until a block, the face or a hint holds it."""
    examples = {key for values in EXAMPLES.values() for key in values}
    placed = set(blocks.FACE_FIELDS) | set(blocks.HINT_FIELDS) | examples
    assert {f.name for f in dataclasses.fields(Card)} <= placed


def test_the_coverage_set_comes_back_the_same():
    for name, entry in coverage.load_snapshot().items():
        card = serial.load(Card, {**coverage._DEFAULT, **entry["read"], "name": name})
        _round_trips(card)


def test_the_reference_deck_comes_back_the_same():
    for card in (chainer.DECK.commander, *chainer.DECK.library):
        _round_trips(card)


def test_a_lander_token_is_a_search_with_costs():
    """LANDER: {2}, {T}, sacrifice it - three cost atoms in front of one verb."""
    ability = blocks._land_search_ability(LANDER, "land_search")
    assert ability.kind == blocks.ACTIVATED
    assert {atom.atom for atom in ability.costs} == {"tap", "mana", "sacrifice_self"}
    assert [effect.verb for effect in ability.effects] == ["search_land"]


def test_ids_number_the_abilities_of_one_kind():
    found = blocks.to_blocks(dataclasses.replace(BASE, **EXAMPLES["mana_abilities"],
                                                 **EXAMPLES["upkeep"]))
    assert [ability.id for ability in found.abilities] == ["mana:0", "mana:1", "triggered:0"]


# --- stable ids -------------------------------------------------------------------

GREEN = ManaAbility(FLAT, (("G", 1),))
TWO_GREEN = ManaAbility(FLAT, (("G", 2),))
COLORLESS = ManaAbility(FLAT, (("C", 1),))
DRAW = Trigger("landfall", draw=1)


def _reconcile(old: dict, new: dict) -> dict:
    return blocks.reconcile(blocks.to_blocks(dataclasses.replace(BASE, **old)),
                            blocks.to_blocks(dataclasses.replace(BASE, **new)))


def test_an_unchanged_ability_keeps_its_id():
    assert _reconcile({"mana_abilities": (GREEN,)},
                      {"mana_abilities": (GREEN,)}) == {"mana:0": ("mana:0", blocks.SAME)}


def test_an_ability_added_in_front_moves_the_old_one():
    assert _reconcile({"mana_abilities": (GREEN,)}, {"mana_abilities": (COLORLESS, GREEN)}) \
        == {"mana:0": ("mana:1", blocks.MOVED)}


def test_a_changed_number_asks_to_check_the_correction():
    assert _reconcile({"mana_abilities": (GREEN,)}, {"mana_abilities": (TWO_GREEN,)}) \
        == {"mana:0": ("mana:0", blocks.CHANGED)}


def test_text_changed_elsewhere_leaves_the_id_alone():
    assert _reconcile({"mana_abilities": (GREEN,), "triggers": (DRAW,)},
                      {"mana_abilities": (GREEN,), "triggers": (Trigger("landfall", draw=2),)}) \
        == {"mana:0": ("mana:0", blocks.SAME), "triggered:0": ("triggered:0", blocks.CHANGED)}


def test_an_ability_that_is_gone_is_said_to_be():
    assert _reconcile({"triggers": (DRAW,)}, {"mana_abilities": (GREEN,)}) \
        == {"triggered:0": (None, blocks.GONE)}


def test_a_changed_ability_is_not_matched_to_an_unrelated_one():
    """A mana ability that became something else does not take the patch along."""
    assert _reconcile({"mana_abilities": (GREEN,)},
                      {"sacrifice_mana": AdditionalCost(sacrifice=frozenset({"creature"})),
                       "sacrifice_mana_amount": 1, "sacrifice_mana_color": "B"}) \
        == {"mana:0": (None, blocks.GONE)}
