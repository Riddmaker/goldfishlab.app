"""Tests des Mana-Modells.

Schwerpunkt: die drei Dinge, die generische Simulations-Bibliotheken falsch
machen - Farbe, Cabal Coffers und Urborg/Crypt Ghast.
"""

import unittest

from simulation.cards import SPELLS, SWAMP, UTILITY_LANDS, Card, LAND
from simulation.mana import (ManaPool, available_mana, count_swamps,
                             effective_cost, has_urborg)

COFFERS = next(c for c in UTILITY_LANDS if c.name == "Cabal Coffers")
URBORG = next(c for c in UTILITY_LANDS if c.name == "Urborg, Tomb of Yawgmoth")
TOWER = next(c for c in UTILITY_LANDS if c.name == "Phyrexian Tower")
BOG = next(c for c in UTILITY_LANDS if c.name == "Bojuka Bog")

SOL_RING = next(c for c in SPELLS if c.name == "Sol Ring")
SIGNET = next(c for c in SPELLS if c.name == "Arcane Signet")
MIND_STONE = next(c for c in SPELLS if c.name == "Mind Stone")
NECROPOTENCE = next(c for c in SPELLS if c.name == "Necropotence")
CRYPT_GHAST = next(c for c in SPELLS if c.name == "Crypt Ghast")


class TestManaPool(unittest.TestCase):
    """Farbige Kosten müssen zwingend mit schwarzem Mana bezahlt werden."""

    def test_colorless_cannot_pay_black_pips(self):
        """Der wichtigste Test: 3 farblose Mana casten kein Necropotence."""
        pool = ManaPool(black=0, colorless=3)
        self.assertFalse(pool.can_pay(3, 0))

    def test_black_can_pay_black_pips(self):
        pool = ManaPool(black=3, colorless=0)
        self.assertTrue(pool.can_pay(3, 0))

    def test_colorless_can_pay_generic(self):
        pool = ManaPool(black=1, colorless=2)
        self.assertTrue(pool.can_pay(1, 2))

    def test_pay_prefers_colorless_for_generic(self):
        """Schwarzes Mana soll für farbige Kosten aufgespart werden."""
        pool = ManaPool(black=2, colorless=2)
        pool.pay(1, 2)
        self.assertEqual((pool.black, pool.colorless), (1, 0))

    def test_pay_falls_back_to_black_for_generic(self):
        pool = ManaPool(black=3, colorless=0)
        pool.pay(1, 1)
        self.assertEqual((pool.black, pool.colorless), (1, 0))

    def test_pay_raises_when_unaffordable(self):
        pool = ManaPool(black=1, colorless=0)
        with self.assertRaises(ValueError):
            pool.pay(2, 0)

    def test_sol_ring_alone_cannot_cast_necropotence(self):
        """Sol Ring in Runde 1 macht 2 farblose Mana - kein {B}{B}{B}."""
        pool = available_mana([SWAMP], [SWAMP], [SOL_RING], crypt_ghast=False)
        self.assertEqual(pool.total, 3)
        self.assertFalse(pool.can_pay(NECROPOTENCE.pips, NECROPOTENCE.generic))


class TestLandMana(unittest.TestCase):
    """Länder, Urborg und Crypt Ghast."""

    def test_basic_swamps(self):
        lands = [SWAMP] * 4
        pool = available_mana(lands, lands, [], crypt_ghast=False)
        self.assertEqual((pool.black, pool.colorless), (4, 0))

    def test_phyrexian_tower_is_colorless(self):
        lands = [SWAMP, TOWER]
        pool = available_mana(lands, lands, [], crypt_ghast=False)
        self.assertEqual((pool.black, pool.colorless), (1, 1))

    def test_bojuka_bog_taps_for_black_but_is_no_swamp(self):
        """Bog erzeugt {B}, zählt aber ohne Urborg nicht als Sumpf."""
        lands = [SWAMP, BOG]
        pool = available_mana(lands, lands, [], crypt_ghast=False)
        self.assertEqual(pool.black, 2)
        self.assertEqual(count_swamps(lands), 1)

    def test_urborg_makes_every_land_a_swamp(self):
        lands = [SWAMP, TOWER, URBORG]
        self.assertTrue(has_urborg(lands))
        self.assertEqual(count_swamps(lands), 3)

    def test_urborg_turns_tower_black(self):
        """Mit Urborg tappt auch Phyrexian Tower für {B}."""
        lands = [SWAMP, TOWER, URBORG]
        pool = available_mana(lands, lands, [], crypt_ghast=False)
        self.assertEqual((pool.black, pool.colorless), (3, 0))

    def test_crypt_ghast_doubles_swamps_only(self):
        """Crypt Ghast verdoppelt Sümpfe, nicht Rocks."""
        lands = [SWAMP] * 4
        pool = available_mana(lands, lands, [SOL_RING], crypt_ghast=True)
        self.assertEqual(pool.black, 8)       # 4 Sümpfe x 2
        self.assertEqual(pool.colorless, 2)   # Sol Ring unverändert


class TestCabalCoffers(unittest.TestCase):
    """Coffers kostet {2} und lohnt erst ab 3 Sümpfen."""

    def test_coffers_not_activated_with_two_swamps(self):
        """Bei 2 Sümpfen ist die Aktivierung ein Nullsummenspiel."""
        lands = [SWAMP, SWAMP, COFFERS]
        pool = available_mana(lands, lands, [], crypt_ghast=False)
        self.assertEqual(pool.total, 2)

    def test_coffers_with_five_swamps(self):
        """5 Sümpfe: 5 Mana, davon 2 für Coffers, dann +5 = 8."""
        lands = [SWAMP] * 5 + [COFFERS]
        pool = available_mana(lands, lands, [], crypt_ghast=False)
        self.assertEqual(pool.black, 8)

    def test_coffers_with_urborg(self):
        """Mit Urborg zählen alle 6 Länder als Sumpf."""
        lands = [SWAMP] * 4 + [URBORG, COFFERS]
        pool = available_mana(lands, lands, [], crypt_ghast=False)
        self.assertEqual(count_swamps(lands), 6)
        self.assertEqual(pool.black, 9)       # 5 Basis - 2 + 6

    def test_coffers_needs_two_mana_available(self):
        """Ohne 2 verfügbares Mana kann Coffers nicht aktiviert werden."""
        lands = [COFFERS]
        pool = available_mana(lands, lands, [], crypt_ghast=False)
        self.assertEqual(pool.total, 0)


class TestJetMedallion(unittest.TestCase):
    """Medallion senkt nur den generischen Anteil schwarzer Zauber."""

    def test_reduces_generic_of_black_spell(self):
        ghast_cost = effective_cost(CRYPT_GHAST, medallion=True)
        self.assertEqual(ghast_cost, (1, 2))      # {3}{B} -> {2}{B}

    def test_never_reduces_coloured_pips(self):
        """Necropotence bleibt {B}{B}{B}, Medallion hilft nicht."""
        self.assertEqual(effective_cost(NECROPOTENCE, medallion=True), (3, 0))

    def test_does_not_reduce_colorless_artifacts(self):
        """Sol Ring ist kein schwarzer Zauber."""
        self.assertEqual(effective_cost(SOL_RING, medallion=True), (0, 1))

    def test_generic_never_goes_negative(self):
        free = Card("Test", 1, 1, 0, LAND)
        self.assertEqual(effective_cost(free, medallion=True), (1, 0))


if __name__ == "__main__":
    unittest.main()
