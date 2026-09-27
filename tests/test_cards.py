"""Tests der Deckzusammensetzung gegen die offiziellen Commander-Regeln."""

import unittest
from collections import Counter

from simulation.cards import (COMMANDER, SPELLS, SWAMP_COUNT, UTILITY_LANDS,
                              build_deck, land_count)


class TestDeckComposition(unittest.TestCase):
    """Commander verlangt exakt 100 Karten inkl. Commander und Singleton."""

    def setUp(self):
        self.deck = build_deck()

    def test_deck_has_99_cards(self):
        """99 Karten plus Commander in der Command Zone ergeben 100."""
        self.assertEqual(len(self.deck), 99)

    def test_total_with_commander_is_100(self):
        self.assertEqual(len(self.deck) + 1, 100)

    def test_land_count_is_35(self):
        """deck-v2.md: 31 Sümpfe + 4 Utility-Länder."""
        self.assertEqual(land_count(self.deck), 35)
        self.assertEqual(len(UTILITY_LANDS), 4)
        self.assertEqual(SWAMP_COUNT, 31)

    def test_spell_count_is_64(self):
        self.assertEqual(len(SPELLS), 64)

    def test_singleton_except_basic_lands(self):
        """Ausser Basislanden darf kein Name doppelt vorkommen."""
        counts = Counter(card.name for card in self.deck)
        duplicates = {name: n for name, n in counts.items()
                      if n > 1 and name != "Swamp"}
        self.assertEqual(duplicates, {})

    def test_commander_not_in_deck(self):
        """Chainer liegt in der Command Zone, nicht in den 99."""
        self.assertNotIn(COMMANDER.name, [card.name for card in self.deck])

    def test_mana_values_are_consistent(self):
        """mv muss immer pips + generic sein, sonst rechnet das Mana falsch."""
        for card in SPELLS:
            with self.subTest(card=card.name):
                self.assertEqual(card.mv, card.pips + card.generic)

    def test_commander_cost_matches_scryfall(self):
        """Chainer kostet {3}{B}{B}."""
        self.assertEqual((COMMANDER.mv, COMMANDER.pips, COMMANDER.generic),
                         (5, 2, 3))

    def test_necropotence_is_triple_black(self):
        """{B}{B}{B} - der Grund, warum Farbe modelliert werden muss."""
        necro = next(c for c in SPELLS if c.name == "Necropotence")
        self.assertEqual((necro.pips, necro.generic), (3, 0))

    def test_removal_is_not_goldfish_castable(self):
        """Ohne Gegner haben Removal und Wipes kein legales Ziel."""
        for name in ("Terror", "Infernal Grasp", "Toxic Deluge", "Mutilate",
                     "Snuff Out", "Tragic Slip"):
            with self.subTest(card=name):
                card = next(c for c in SPELLS if c.name == name)
                self.assertFalse(card.goldfish_castable)

    def test_tapped_lands_flagged(self):
        """Bojuka Bog kommt getappt - in Runde 1-3 entscheidend."""
        bog = next(c for c in UTILITY_LANDS if c.name == "Bojuka Bog")
        self.assertTrue(bog.enters_tapped)


if __name__ == "__main__":
    unittest.main()
