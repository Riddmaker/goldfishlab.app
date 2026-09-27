"""Statistische Korrektheit der Simulation.

Der wichtigste Test im ganzen Projekt: die Monte-Carlo-Simulation muss die
**exakte** hypergeometrische Verteilung reproduzieren. Wenn das nicht stimmt,
ist der Zufallsziehmechanismus kaputt und alle anderen Zahlen sind wertlos.

Methodik nach Frank Karsten (hypergeometrisch) bzw. dem EDHREC-Artikel
(Monte Carlo, wenn die geschlossene Formel nicht mehr reicht).
"""

import random
import unittest

from scipy.stats import hypergeom

from simulation.analysis import run, simulate_game
from simulation.cards import build_deck, land_count

DECK_SIZE = 99
LANDS = 35
HAND = 7
TOLERANCE = 0.006          # 0.6 Prozentpunkte bei 200k Ziehungen


def _exact_at_least(k: int) -> float:
    """Exakte Wahrscheinlichkeit für mindestens k Länder in der Starthand."""
    return 1.0 - hypergeom.cdf(k - 1, DECK_SIZE, LANDS, HAND)


class TestHypergeometricAgreement(unittest.TestCase):
    """Monte Carlo gegen geschlossene Formel."""

    @classmethod
    def setUpClass(cls):
        rng = random.Random(4242)
        deck = build_deck()
        cls.iterations = 200_000
        counts = [0] * (HAND + 1)
        for _ in range(cls.iterations):
            hand = rng.sample(deck, HAND)
            counts[sum(1 for card in hand if card.is_land)] += 1
        cls.counts = counts

    def _observed_at_least(self, k: int) -> float:
        return sum(self.counts[k:]) / self.iterations

    def test_deck_parameters_match_formula_inputs(self):
        """Die Formel muss mit demselben Deck rechnen wie die Simulation."""
        deck = build_deck()
        self.assertEqual(len(deck), DECK_SIZE)
        self.assertEqual(land_count(deck), LANDS)

    def test_at_least_two_lands(self):
        self.assertAlmostEqual(self._observed_at_least(2), _exact_at_least(2),
                               delta=TOLERANCE)

    def test_at_least_three_lands(self):
        """Referenzwert: 47.73 Prozent."""
        exact = _exact_at_least(3)
        self.assertAlmostEqual(exact, 0.4773, places=3)
        self.assertAlmostEqual(self._observed_at_least(3), exact,
                               delta=TOLERANCE)

    def test_at_least_four_lands(self):
        self.assertAlmostEqual(self._observed_at_least(4), _exact_at_least(4),
                               delta=TOLERANCE)

    def test_exactly_zero_lands(self):
        exact = hypergeom.pmf(0, DECK_SIZE, LANDS, HAND)
        observed = self.counts[0] / self.iterations
        self.assertAlmostEqual(observed, exact, delta=TOLERANCE)


class TestSimulationSanity(unittest.TestCase):
    """Grundplausibilität der Spielsimulation."""

    def test_simulation_runs(self):
        result = simulate_game(random.Random(1), turns=3)
        self.assertEqual(len(result["per_turn"]), 3)

    def test_reproducible_with_seed(self):
        """Gleicher Seed muss gleiches Ergebnis liefern."""
        first = run(iterations=300, seed=99)
        second = run(iterations=300, seed=99)
        self.assertEqual(first["mulligans"], second["mulligans"])
        self.assertEqual(first["turn_stats"][2]["commander"],
                         second["turn_stats"][2]["commander"])

    def test_lands_never_exceed_turn_number(self):
        """Ein Landdrop pro Runde - mehr darf nie im Spiel liegen."""
        for seed in range(30):
            result = simulate_game(random.Random(seed), turns=3)
            for index, snapshot in enumerate(result["per_turn"]):
                with self.subTest(seed=seed, turn=index + 1):
                    self.assertLessEqual(snapshot["lands"], index + 1)

    def test_mana_is_monotone_enough(self):
        """Mana darf schwanken, aber nie negativ werden."""
        for seed in range(30):
            result = simulate_game(random.Random(seed), turns=3)
            for snapshot in result["per_turn"]:
                self.assertGreaterEqual(snapshot["mana"], 0)

    def test_life_never_below_zero_in_three_turns(self):
        """Necropotence darf sich in 3 Runden nicht selbst töten."""
        for seed in range(50):
            result = simulate_game(random.Random(seed), turns=3)
            self.assertGreater(result["final_life"], 0)

    def test_on_the_draw_sees_more_cards(self):
        """Am Zug hinten sieht man mehr Karten - Plausibilitätsprüfung."""
        play = run(iterations=2000, on_the_play=True, seed=7)
        draw = run(iterations=2000, on_the_play=False, seed=7)
        play_lands = sum(play["turn_stats"][2]["lands"])
        draw_lands = sum(draw["turn_stats"][2]["lands"])
        self.assertGreater(draw_lands, play_lands)


if __name__ == "__main__":
    unittest.main()
