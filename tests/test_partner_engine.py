"""Two commanders in the engine (P20 F2b, engine v28).

Lauf 1 imported Krark and Thrasios as partners and the engine knew one
commander. Now each is cast from the command zone with its own tax, the
cheaper one that can be paid first, and "commander on the battlefield" means
at least one - with a row for the games that had both. A deck with one
commander plays exactly as in v27: the golden parity tests hold that.
"""

import random

from simulation import actions, agent, analysis, serial
from simulation.cards import CREATURE, LAND, Card, DeckDefinition
from simulation.game import Game
from simulation.manacost import parse
from simulations.report import milestones

FOREST = Card("Forest", 0, 0, 0, LAND, subtypes=frozenset({"forest"}), basic=True)
KRARK = Card("Krark", 2, 0, 1, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}),
             colors=frozenset({"G"}))
THRASIOS = Card("Thrasios", 3, 0, 2, CREATURE, cost=parse("{2}{G}"),
                types=frozenset({"creature"}), colors=frozenset({"G"}))
FILLER = Card("Filler", 6, 0, 5, CREATURE, cost=parse("{5}{G}"), types=frozenset({"creature"}),
              colors=frozenset({"G"}))


def deck(partner=THRASIOS):
    return DeckDefinition(name="Pair", commander=KRARK, partner=partner,
                          library=tuple([FOREST] * 40 + [FILLER] * 59))


def test_the_command_zone_holds_both_first_commander_first():
    assert deck().commanders == (KRARK, THRASIOS)
    assert deck(partner=None).commanders == (KRARK,)
    assert deck().canonical().partner is THRASIOS


def test_each_commander_pays_its_own_tax():
    game = Game(random.Random(1), deck=deck())
    game.partner_casts = 1

    assert str(game._commander_cost(KRARK)) == "{1}{G}"
    assert str(game._commander_cost(THRASIOS)) == "{4}{G}"


def test_the_cheaper_castable_commander_comes_first():
    game = Game(random.Random(1), deck=deck())
    game.lands = [FOREST] * 3

    assert game.castable_commanders(game.mana()) == [KRARK, THRASIOS]
    game.lands = [FOREST] * 2
    assert game.castable_commanders(game.mana()) == [KRARK]


def test_both_are_legal_actions_and_the_second_is_cast_by_index():
    game = Game(random.Random(1), deck=deck())
    game.take_opening_hand()
    actions.apply(game, actions.KeepHand())
    game.lands = [FOREST] * 5
    actions.apply(game, actions.AdvancePhase())
    while game.phase != "main1" and game.phase is not None:
        actions.apply(game, actions.AdvancePhase())

    legal = actions.legal_actions(game)
    assert actions.CastCommander(index=0) in legal
    assert actions.CastCommander(index=1) in legal

    actions.apply(game, actions.CastCommander(index=1))
    assert game.has("Thrasios") and not game.has("Krark")
    assert game.partner_casts == 1 and game.commander_casts == 0


def test_the_agent_casts_both_over_a_game():
    game = Game(random.Random(3), deck=deck())
    game.take_opening_hand()
    for _ in range(6):
        agent.take_turn(game)

    assert game.has("Krark") and game.has("Thrasios")


def test_the_report_counts_one_and_both():
    result = analysis.run(iterations=40, turns=6, seed=5, deck=deck())

    last = result["turn_stats"][-1]
    assert last["both_commanders"] > 0
    assert last["commander"] >= last["both_commanders"]
    rows = {row["key"]: row for row in milestones(analysis.as_json(result))}
    assert "both_commanders" in rows and rows["both_commanders"]["label"] == (
        "Both commanders on the battlefield")


def test_a_deck_with_one_commander_keeps_its_result_shape():
    result = analysis.run(iterations=10, turns=3, seed=5, deck=deck(partner=None))

    assert "both_commanders" not in result["turn_stats"][0]


def test_a_stored_playtest_state_keeps_the_second_tax_and_reads_old_ones():
    game = Game(random.Random(1), deck=deck())
    game.partner_casts = 2

    state = serial.dump_game(game)
    assert serial.load_game(state, deck()).partner_casts == 2
    del state["partner_casts"]
    assert serial.load_game(state, deck()).partner_casts == 0
