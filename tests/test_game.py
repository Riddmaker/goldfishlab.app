"""Tests für Mulligan-Regeln und Rundenablauf."""

import random
import unittest

from simulation import agent
from simulation.cards import SPELLS, SWAMP, UTILITY_LANDS
from simulation.game import Game

DARK_RITUAL = next(c for c in SPELLS if c.name == "Dark Ritual")
NECROPOTENCE = next(c for c in SPELLS if c.name == "Necropotence")
SOL_RING = next(c for c in SPELLS if c.name == "Sol Ring")
GRAVE_PACT = next(c for c in SPELLS if c.name == "Grave Pact")
TERROR = next(c for c in SPELLS if c.name == "Terror")
ANIMATE_DEAD = next(c for c in SPELLS if c.name == "Animate Dead")
CARRION_FEEDER = next(c for c in SPELLS if c.name == "Carrion Feeder")
BOG = next(c for c in UTILITY_LANDS if c.name == "Bojuka Bog")


class TestMulliganRules(unittest.TestCase):
    """Im Mehrspieler-Commander ist der erste Mulligan gratis."""

    def test_first_mulligan_is_free(self):
        """Der entscheidende Regelpunkt: nach 1 Mulligan bleiben 7 Karten."""
        self.assertEqual(Game.cards_to_bottom(1), 0)

    def test_no_mulligan_bottoms_nothing(self):
        self.assertEqual(Game.cards_to_bottom(0), 0)

    def test_second_mulligan_bottoms_one(self):
        self.assertEqual(Game.cards_to_bottom(2), 1)

    def test_third_mulligan_bottoms_two(self):
        self.assertEqual(Game.cards_to_bottom(3), 2)

    def test_opening_hand_size_respects_free_mulligan(self):
        """Nach echten Mulligans muss die Handgrösse zur Regel passen."""
        for seed in range(50):
            game = Game(random.Random(seed))
            game.take_opening_hand()
            expected = 7 - Game.cards_to_bottom(game.mulligans)
            with self.subTest(seed=seed):
                self.assertEqual(len(game.hand), expected)


class TestKeepHeuristic(unittest.TestCase):
    """Die Keep-Regel bestimmt alle nachgelagerten Zahlen."""

    def setUp(self):
        self.game = Game(random.Random(1))

    def test_keeps_three_landers(self):
        hand = [SWAMP] * 3 + [NECROPOTENCE, GRAVE_PACT, TERROR, SOL_RING]
        self.assertTrue(self.game.keepable(hand))

    def test_mulligans_zero_landers(self):
        hand = [NECROPOTENCE, GRAVE_PACT, TERROR, SOL_RING, DARK_RITUAL,
                CARRION_FEEDER, ANIMATE_DEAD]
        self.assertFalse(self.game.keepable(hand))

    def test_mulligans_six_landers(self):
        hand = [SWAMP] * 6 + [NECROPOTENCE]
        self.assertFalse(self.game.keepable(hand))

    def test_keeps_one_lander_with_two_accelerants(self):
        hand = [SWAMP, SOL_RING, DARK_RITUAL, NECROPOTENCE, GRAVE_PACT,
                TERROR, CARRION_FEEDER]
        self.assertTrue(self.game.keepable(hand))

    def test_mulligans_one_lander_without_acceleration(self):
        hand = [SWAMP, NECROPOTENCE, GRAVE_PACT, TERROR, CARRION_FEEDER,
                ANIMATE_DEAD, SOL_RING]
        self.assertFalse(self.game.keepable(hand))


class TestTurnSequence(unittest.TestCase):
    """Rundenablauf, Landdrops und getappte Länder."""

    def test_no_draw_on_turn_one_on_the_play(self):
        game = Game(random.Random(3), on_the_play=True)
        game.take_opening_hand()
        before = len(game.hand)
        game.begin_turn()
        self.assertEqual(len(game.hand), before)

    def test_draw_on_turn_one_on_the_draw(self):
        game = Game(random.Random(3), on_the_play=False)
        game.take_opening_hand()
        before = len(game.hand)
        game.begin_turn()
        self.assertEqual(len(game.hand), before + 1)

    def test_one_land_drop_per_turn(self):
        game = Game(random.Random(5))
        game.take_opening_hand()
        agent.take_turn(game)
        self.assertLessEqual(len(game.lands), 1)

    def test_tapped_land_gives_no_mana_this_turn(self):
        """Bojuka Bog darf in der Runde, in der es gespielt wird, nichts geben."""
        game = Game(random.Random(7))
        game.hand = [BOG]
        game.begin_turn()
        game.play_land(BOG)
        self.assertEqual(game.mana().total, 0)

    def test_tapped_land_untaps_next_turn(self):
        game = Game(random.Random(7))
        game.hand = [BOG]
        game.begin_turn()
        game.play_land(BOG)
        game.begin_turn()
        self.assertEqual(game.mana().total, 1)

    def test_removal_is_never_cast_without_targets(self):
        """Der Agent darf im Goldfish kein Mana für Removal verbrennen."""
        game = Game(random.Random(11))
        game.hand = [SWAMP, SWAMP, TERROR]
        for _ in range(2):
            agent.take_turn(game)
        self.assertIn(TERROR, game.hand)

    def test_reanimation_blocked_with_empty_graveyard(self):
        game = Game(random.Random(13))
        game.hand = [SWAMP, SWAMP, ANIMATE_DEAD]
        pool = game.mana()
        self.assertFalse(game.can_cast(ANIMATE_DEAD, pool))


class TestRitualLine(unittest.TestCase):
    """Dark Ritual soll nur gecastet werden, wenn es etwas freischaltet."""

    def test_turn_one_dark_ritual_into_necropotence(self):
        """Sumpf + Dark Ritual + Necropotence ist der beste Start des Decks."""
        game = Game(random.Random(17), on_the_play=True)
        game.hand = [SWAMP, DARK_RITUAL, NECROPOTENCE]
        agent.take_turn(game)
        self.assertTrue(game.has("Necropotence"))

    def test_ritual_not_wasted_without_payoff(self):
        """Ohne lohnendes Ziel bleibt Dark Ritual auf der Hand."""
        game = Game(random.Random(19), on_the_play=True)
        game.hand = [SWAMP, DARK_RITUAL, CARRION_FEEDER]
        agent.take_turn(game)
        self.assertIn(DARK_RITUAL, game.hand)


class TestCommanderTax(unittest.TestCase):
    """Chainer kostet {3}{B}{B}, danach je +{2}."""

    def test_commander_castable_with_five_mana(self):
        game = Game(random.Random(23))
        game.lands = [SWAMP] * 5
        self.assertTrue(game.can_cast_commander(game.mana()))

    def test_commander_not_castable_with_four(self):
        game = Game(random.Random(23))
        game.lands = [SWAMP] * 4
        self.assertFalse(game.can_cast_commander(game.mana()))

    def test_tax_applies_after_first_cast(self):
        game = Game(random.Random(23))
        game.lands = [SWAMP] * 7
        game.cast_commander(game.mana())
        game.creatures.clear()          # Chainer stirbt, zurück in die Zone
        self.assertEqual(game.commander_casts, 1)
        game.lands = [SWAMP] * 6
        self.assertFalse(game.can_cast_commander(game.mana()))
        game.lands = [SWAMP] * 7
        self.assertTrue(game.can_cast_commander(game.mana()))


if __name__ == "__main__":
    unittest.main()
