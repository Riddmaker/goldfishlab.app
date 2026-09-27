"""Engine version 3: mana sources that cost something, happen once, or walk.

Three shapes the engine could not play before the 2026-09-25 review, each of
which made a deck look different from how it plays:

* **Mana creatures** never tapped for mana. `Game.mana()` read lands and rocks
  only, so a Llanowar Elves resolved and did nothing, while the tune page said
  it tapped for green.
* **A cost beside the tap** was never paid. A Signet (`{1}, {T}: Add {U}{B}`)
  made two mana of one colour for free instead of one net mana in both.
* **"Doesn't untap during your untap step"** was never read. Mana Vault made
  three every turn instead of once.

And one in the payer: **Phyrexian symbols** took mana before the generic part
was looked at, so a spell a player casts by paying two life read as uncastable.

None of these cards is in the reference deck, which is why the golden parity
snapshot does not move - and that it does not move is the evidence nothing else
did. These tests pin the new behaviour instead.
"""

import random

from simulation import actions, analysis
from simulation.cards import (
    CREATURE,
    FLAT,
    LAND,
    ROCK,
    SORCERY,
    Card,
    DeckDefinition,
    ManaAbility,
)
from simulation.game import Game
from simulation.mana import ManaPool, available_mana
from simulation.manacost import ManaCost, parse, plan_payment

SWAMP = Card("Swamp", 0, 0, 0, LAND, subtypes=frozenset({"swamp"}))
ISLAND = Card("Island", 0, 0, 0, LAND, subtypes=frozenset({"island"}))
ELVES = Card("Llanowar Elves", 1, 0, 0, CREATURE,
             mana_abilities=(ManaAbility(FLAT, {"G": 1}),), cost=parse("{G}"))
SIGNET = Card("Dimir Signet", 2, 0, 2, ROCK,
              mana_abilities=(ManaAbility(FLAT, {"U": 1, "B": 1}, activation_generic=1),))
VAULT = Card("Mana Vault", 1, 0, 1, ROCK,
             mana_abilities=(ManaAbility(FLAT, {"C": 3}),), untaps=False)
FILLER = Card("Filler", 9, 0, 9, SORCERY)


def game_with(**zones) -> Game:
    """A game with nothing in it but the permanents named."""
    deck = DeckDefinition(name="test", commander=None, library=(FILLER,) * 40)
    game = Game(random.Random(1), deck=deck)
    for zone, cards in zones.items():
        setattr(game, zone, list(cards))
    return game


# --- mana creatures ---------------------------------------------------------

def test_a_mana_creature_on_the_battlefield_taps_for_mana():
    game = game_with(lands=[SWAMP], creatures=[ELVES])
    assert game.mana().by_color() == {"B": 1, "G": 1}


def test_a_creature_cast_this_turn_adds_nothing_until_the_next():
    """Summoning sickness, for free: the pool opened before it resolved."""
    forest = Card("Forest", 0, 0, 0, LAND, subtypes=frozenset({"forest"}))
    game = game_with(lands=[SWAMP], hand=[forest, ELVES])
    actions.apply(game, actions.BeginTurn())
    actions.apply(game, actions.PlayLand(index=0))
    actions.apply(game, actions.OpenMainPhase())
    actions.apply(game, actions.CastSpell(index=0))

    assert ELVES in game.creatures
    assert game.mana_available == 2

    actions.apply(game, actions.BeginTurn())
    actions.apply(game, actions.OpenMainPhase())
    assert game.mana_available == 3


# --- a cost beside the tap ---------------------------------------------------

def test_a_signet_nets_one_mana_in_both_of_its_colours():
    pool = available_mana([SWAMP, ISLAND], [SWAMP, ISLAND], [SIGNET], 0)
    assert pool.total == 3
    assert pool.amount("U") >= 1 and pool.amount("B") >= 1


def test_a_signet_with_nothing_to_pay_for_it_makes_nothing():
    assert available_mana([], [], [SIGNET], 0).total == 0


def test_a_signet_is_fixing_and_not_fast_mana():
    """{2} for a net one: the fast-mana milestone must not count it."""
    assert not analysis._has_fast_mana([SIGNET])
    assert analysis._has_fast_mana([Card("Sol Ring", 1, 0, 1, ROCK,
                                         mana_abilities=(ManaAbility(FLAT, {"C": 2}),))])


# --- once, and then it stays tapped -------------------------------------------

def test_mana_vault_makes_its_mana_once():
    game = game_with(lands=[SWAMP], rocks=[VAULT])

    actions.apply(game, actions.BeginTurn())
    actions.apply(game, actions.OpenMainPhase())
    assert game.mana_available == 4

    actions.apply(game, actions.BeginTurn())
    actions.apply(game, actions.OpenMainPhase())
    assert game.mana_available == 1, "the Vault did not untap"
    assert game.stays_tapped == [VAULT]


def test_a_permanent_that_untaps_is_never_held():
    game = game_with(lands=[SWAMP], rocks=[SIGNET])
    for _ in range(3):
        actions.apply(game, actions.BeginTurn())
        actions.apply(game, actions.OpenMainPhase())
    assert game.stays_tapped == []


# --- Phyrexian mana -------------------------------------------------------------

def test_phyrexian_mana_is_paid_with_life_when_the_mana_is_needed_elsewhere():
    """Phyrexian Metamorph from three blue: {3} from the mana, {U/P} from life."""
    payment = plan_payment({"U": 3}, parse("{3}{U/P}"), life=40)
    assert payment is not None
    assert payment.life == 2
    assert payment.total == 3


def test_phyrexian_mana_is_paid_with_mana_when_there_is_enough():
    payment = plan_payment({"U": 4}, parse("{3}{U/P}"), life=40)
    assert (payment.life, payment.total) == (0, 4)


def test_dismember_from_two_black_pays_one_symbol_each_way():
    payment = plan_payment({"B": 2}, parse("{1}{B/P}{B/P}"), life=40)
    assert (payment.life, payment.total) == (2, 2)


def test_the_life_floor_still_holds():
    """Never below 25 for Phyrexian mana, whatever the pool."""
    assert plan_payment({"U": 3}, parse("{3}{U/P}"), life=26) is None


def test_the_pool_agrees_with_the_payer():
    assert ManaPool(U=3).can_pay_cost(ManaCost(generic=3, phyrexian=("U",)), life=40)
