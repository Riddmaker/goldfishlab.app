"""Engine v34: cards drawn by a count.

"You draw X cards and you lose X life, where X is your devotion to black"
(Minions' Murmurs), "Draw cards equal to the greatest power among creatures
you control" (Rishkar's Expertise), Champion of Dusk's Vampires as it enters.
The count is the board's as the card resolves. Where the spell does more
than the engine plays (Rishkar's free spell), the draw is read and the card
still says so. Not a target player's draw (Monumental Corruption).
"""

import random

import pytest

from cards import profiles
from cards.models import DerivedProfile, OracleCard
from cards.profiles import _counted_draw
from simulation import actions, build
from simulation.cards import CREATURE, SORCERY, DeckDefinition, Trigger
from simulation.game import Game
from simulation.manacost import parse
from simulation.rules.amount import Amount
from simulation.rules.selector import Selector
from tests.engine_builders import tap

Kind = DerivedProfile.Kind

MURMURS = ("You draw X cards and you lose X life, where X is your devotion to black. (Each {B} "
           "in the mana costs of permanents you control counts toward your devotion to black.)")
EXPERTISE = ("Draw cards equal to the greatest power among creatures you control.\nYou may cast "
             "a spell with mana value 4 or less from your hand without paying its mana cost.")
CHAMPION = ("When this creature enters, you draw X cards and you lose X life, where X is the "
            "number of Vampires you control.")


def card(text, name="Card", type_line="Sorcery"):
    return OracleCard(name=name, front_name=name, oracle_text=text, type_line=type_line)


# --- the reader -----------------------------------------------------------------------


@pytest.mark.parametrize("text, kind, amount, lose", [
    (MURMURS, Kind.SORCERY, {"kind": "devotion", "color": "B"}, True),
    (CHAMPION, Kind.CREATURE, {"kind": "count", "zone": "creatures",
                               "creature_types": ["vampire"]}, True),
    ("Draw cards equal to the number of creatures you control.", Kind.SORCERY,
     {"kind": "count", "zone": "creatures"}, False),
    ("Draw cards equal to the number of instant and sorcery cards in your graveyard.",
     Kind.INSTANT, {"kind": "count", "zone": "graveyard", "types": ["instant", "sorcery"]}, False),
    ("When this creature enters, draw cards equal to half the number of artifacts you control, "
     "rounded down.", Kind.CREATURE, {"kind": "count", "zone": "battlefield",
                                      "types": ["artifact"], "half": True}, False),
    ("Draw cards equal to the greatest mana value among artifacts you control.", Kind.SORCERY,
     {"kind": "stat", "stat": "mv:greatest", "types": ["artifact"]}, False),
    ("As an additional cost to cast this spell, sacrifice a creature.\nDraw cards equal to the "
     "sacrificed creature's power.", Kind.INSTANT, {"kind": "stat", "stat": "sacrificed:power"},
     False),
])
def test_a_count_is_read(text, kind, amount, lose):
    found, more = _counted_draw(card(text), kind)
    assert found == {"amount": amount, "lose": lose} and not more


def test_what_else_the_spell_does_is_said():
    found, more = _counted_draw(card(EXPERTISE), Kind.SORCERY)
    assert found["amount"] == {"kind": "stat", "stat": "power:greatest"} and more


def test_momentous_falls_life_is_only_left_out():
    found, more = _counted_draw(card(
        "As an additional cost to cast this spell, sacrifice a creature.\nYou draw cards equal to "
        "the sacrificed creature's power, then you gain life equal to its toughness."),
        Kind.INSTANT)
    assert found is not None and not more


@pytest.mark.parametrize("text, kind", [
    # A target player's draw: a goldfish's target is its own, never asked.
    ("Target player draws X cards and loses X life, where X is your devotion to black.",
     Kind.SORCERY),
    # A count nothing here reads.
    ("Draw cards equal to the number of cards in target opponent's hand.", Kind.SORCERY),
    # Foods are no creatures.
    ("Draw cards equal to the number of Foods you control.", Kind.SORCERY),
    # An enchantment's draw at another moment.
    ("At the beginning of your upkeep, draw cards equal to the number of creatures you "
     "control.", Kind.ENCHANTMENT),
    # A spell's "when it enters" is another card's.
    ("When this creature enters, draw cards equal to the number of creatures you control.",
     Kind.SORCERY),
])
def test_what_is_not_a_counted_draw(text, kind):
    assert _counted_draw(card(text), kind) == (None, False)


def test_derive_reads_the_draw_and_keeps_a_reason_for_the_rest():
    murmurs = profiles.derive(card(MURMURS, "Minions' Murmurs"), set(), forge=())
    expertise = profiles.derive(card(EXPERTISE, "Rishkar's Expertise"), set(), forge=())
    assert murmurs.draw_by["lose"] and not murmurs.review_reasons
    assert "draws, and does more that the engine does not play" in expertise.review_reasons


def test_a_fixed_draw_is_not_read_twice():
    found = profiles.derive(card("Draw two cards."), set(), forge=())
    assert found.draws_cards == 2 and found.draw_by is None


# --- the engine -------------------------------------------------------------------------


def vampire(name):
    return build.card(name, 2, 0, 2, CREATURE, cost=parse("{1}{B}"),
                      types=frozenset({"creature"}), creature_types=frozenset({"vampire"}))


VAMPIRES = Amount("count", zone="creatures",
                  selector=Selector(creature_types=frozenset({"vampire"})))


def game_with(creatures, library):
    game = Game(random.Random(1), deck=DeckDefinition("v34", None, (vampire("Filler"),)))
    game.creatures, game.library, game.hand = list(creatures), list(library), []
    tap(game, lands=0, rocks=0)
    game.life = 40
    return game


def test_a_spell_draws_by_the_count_and_loses_as_much_life():
    spell = build.card("Counted", 2, 0, 2, SORCERY, cost=parse("{0}"), draw_by=VAMPIRES,
                       lose_drawn=True)
    game = game_with([vampire("A"), vampire("B")], [vampire(f"L{n}") for n in range(5)])
    game.hand = [spell]
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert (len(game.hand), game.life) == (2, 38)


def test_champion_of_dusk_counts_itself():
    champion = build.card("Champion of Dusk", 5, 0, 5, CREATURE, cost=parse("{0}"),
                          types=frozenset({"creature"}), creature_types=frozenset({"vampire"}),
                          triggers=(Trigger(event="resolves", draw_by=VAMPIRES,
                                            lose_drawn=True),))
    game = game_with([vampire("A")], [vampire(f"L{n}") for n in range(5)])
    game.hand = [champion]
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert (len(game.hand), game.life) == (2, 38)


def test_half_the_artifacts_rounded_down():
    half = Amount("count", zone="creatures", half=True)
    from simulation.rules.amount import compile_amount

    game = game_with([vampire("A"), vampire("B"), vampire("C")], [])
    assert compile_amount(half)(game, None) == 1


def test_the_adapter_routes_a_permanents_draw_to_a_trigger():
    from simulations.engine.adapter import _draw_by

    profile = type("Profile", (), {"kind": "creature", "draw_by": {
        "amount": {"kind": "count", "zone": "creatures", "creature_types": ["vampire"]},
        "lose": True}})()
    assert _draw_by(profile) == VAMPIRES
