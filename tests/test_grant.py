"""Engine v33: mana abilities a card gives other permanents.

Until v33 the engine played Cryptolith Rite's grant to every creature,
Abundant Growth's to its land and Paradise Mantle's to the creature it
equips; every other grant said "only grants a mana ability to another
permanent".

What these tests hold:

1. **The reader reads the grant**: to your lands (Wrenn and Realmbreaker),
   to creatures of one type (Tyvar Kell's Elves, Manaweft Sliver), to the
   enchanted creature (Karametra's Favor), and two of one colour on the
   enchanted land (Gift of Paradise).
2. **The engine plays it**: each land fixes; the enchanted land makes one
   more; only creatures of the type tap, "other" ones not the granter; the
   Aura goes on a creature without a mana ability, which taps for it.
3. **The card page says so.**
"""

import pytest

from cards.models import DerivedProfile
from simulation import actions, build
from simulation.cards import CREATURE, ENCHANTMENT, GRANT, Card, ManaAbility
from simulation.manacost import parse
from simulations.engine import adapter
from tests.test_mana_on_top import (
    BEAR,
    ELF,
    FOREST,
    ISLAND,
    WASTES,
    board,
    made,
    permanent,
    rule_of,
)

Kind = DerivedProfile.Kind


# --- the reader -------------------------------------------------------------------------


@pytest.mark.parametrize("text, kind, subtype, produces", [
    ('Lands you control have "{T}: Add one mana of any color."', Kind.PLANESWALKER, "land",
     {"WUBRG": 1}),
    ('Elves you control have "{T}: Add {B}."', Kind.PLANESWALKER, "type:elf", {"B": 1}),
    ('Sliver creatures you control have "{T}: Add one mana of any color."', Kind.CREATURE,
     "type:sliver", {"WUBRG": 1}),
    ('Other Goblins you control have "{T}: Add {R}."', Kind.CREATURE, "other_type:goblin",
     {"R": 1}),
    ('Enchant creature\nEnchanted creature has "{T}: Add one mana of any color."',
     Kind.ENCHANTMENT, "enchanted_creature", {"WUBRG": 1}),
    ('Enchant land\nEnchanted land has "{T}: Add two mana of any one color."',
     Kind.ENCHANTMENT, "enchanted", {"WUBRG": 2}),
])
def test_a_grant_is_read(text, kind, subtype, produces):
    rule = rule_of(text, kind)
    assert (rule["rule"], rule["subtype"], rule["produces"]) == ("grant", subtype, produces)


@pytest.mark.parametrize("text", [
    # Two of one colour for every land is more than a land search: not read.
    'Lands you control have "{T}: Add two mana of any one color."',
    'Enchant land\nEnchanted creature has "{T}: Add one mana of any color."',
])
def test_a_grant_it_does_not_play_is_not_read(text):
    assert rule_of(text) is None


# --- the engine -------------------------------------------------------------------------

REALMBREAKER = permanent("Wrenn and Realmbreaker", GRANT, "land", {"WUBRG": 1})
GIFT = permanent("Gift of Paradise", GRANT, "enchanted", {"WUBRG": 2}, enchants="land")
TYVAR = permanent("Tyvar Kell", GRANT, "type:elf", {"B": 1})
THRANDUIL = build.card("Thranduil", 3, 0, 3, CREATURE, types=frozenset({"creature"}),
                       creature_types=frozenset({"elf"}),
                       mana_abilities=(ManaAbility(GRANT, {"G": 1}, subtype="other_type:elf"),))
FAVOR = build.card("Karametra's Favor", 1, 0, 1, ENCHANTMENT, cost=parse("{G}"),
                   types=frozenset({"enchantment"}), enchants="creature",
                   mana_abilities=(ManaAbility(GRANT, {"WUBRG": 1},
                                               subtype="enchanted_creature"),))
ELVISH = Card("Elvish Visionary", 2, 0, 2, CREATURE, cost=parse("{1}{G}"),
              types=frozenset({"creature"}), creature_types=frozenset({"elf"}))


def test_every_land_fixes():
    assert made(board([FOREST, ISLAND, WASTES], others=[REALMBREAKER])) == {"WUBRG": 3}


def test_the_enchanted_land_makes_two():
    assert made(board([FOREST, WASTES], others=[GIFT])) == {"G": 1, "WUBRG": 2}


def test_only_creatures_of_the_type_tap():
    assert made(board(creatures=[BEAR, ELVISH], others=[TYVAR])) == {"B": 1}


def test_other_ones_leave_the_granter_out():
    assert made(board(creatures=[THRANDUIL, ELVISH])) == {"G": 1}


def test_the_aura_goes_on_a_creature_that_then_taps_for_mana():
    game = board([FOREST], creatures=[ELF, BEAR])
    game.hand = [FAVOR]
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert game.is_host_of(BEAR, FAVOR)
    game.pool = None
    actions.OpenMainPhase().run(game, None)
    assert game.pool.by_color() == {"G": 2, "WUBRG": 1}


# --- the card page ------------------------------------------------------------------------


@pytest.mark.parametrize("card, said", [
    (REALMBREAKER, "your lands tap for one mana of any colour"),
    (GIFT, "the enchanted land taps for two mana of any one colour"),
    (TYVAR, "your Elf creatures tap for 1 B"),
    (THRANDUIL, "your other Elf creatures tap for 1 G"),
])
def test_the_card_page_says_the_grant(card, said):
    assert adapter._ability_text(card.mana.entries[0]) == said


def test_chromatic_lantern_taps_for_its_own_beside_its_grant():
    from tests.test_engine_adapter import _profile

    lantern = _profile(produces_mana=True, mana_amount=1, mana_colors=list("WUBRG"),
                       mana_rule={"rule": "grant", "subtype": "land", "activation": 0,
                                  "color": "", "produces": {"WUBRG": 1}})
    grant, own = adapter._mana_abilities(lantern, {}, "rock", "Chromatic Lantern", [],
                                         frozenset({"G", "U"}))
    assert (grant.rule, grant.subtype) == (GRANT, "land") and own.produces == (("UG", 1),)


@pytest.mark.parametrize("text", [
    'Tokens you control have "{T}: Add {G}."',
    'Foods you control have "{T}: Add one mana of any color."',
])
def test_a_grant_to_what_is_no_creature_type_is_not_read(text):
    assert rule_of(text) is None
