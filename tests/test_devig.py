import math
import unittest

from bettingbot import devig


class TestOverround(unittest.TestCase):
    def test_fair_coin_has_no_margin(self):
        self.assertAlmostEqual(devig.overround([2.0, 2.0]), 0.0)

    def test_typical_football_margin(self):
        # 1.90 / 3.40 / 4.20 ~ 5.2% de margen
        self.assertGreater(devig.overround([1.90, 3.40, 4.20]), 0.04)


class TestMethods(unittest.TestCase):
    odds = [1.90, 3.40, 4.20]

    def test_all_methods_sum_to_one(self):
        for method in devig.METHODS:
            probs = devig.devig(self.odds, method)
            self.assertAlmostEqual(sum(probs), 1.0, places=9, msg=method)

    def test_all_methods_are_ordered(self):
        # El favorito debe seguir siendo el más probable, y el longshot el menos.
        for method in devig.METHODS:
            probs = devig.devig(self.odds, method)
            self.assertGreater(probs[0], probs[1], msg=method)
            self.assertGreater(probs[1], probs[2], msg=method)

    def test_sharper_methods_correct_favourite_longshot_bias(self):
        # El multiplicativo regala probabilidad al longshot. Power y Shin
        # corrigen el sesgo: suben el favorito y bajan el longshot.
        mult = devig.devig(self.odds, "multiplicative")
        power = devig.devig(self.odds, "power")
        shin = devig.devig(self.odds, "shin")
        for name, probs in (("power", power), ("shin", shin)):
            self.assertGreater(probs[0], mult[0], msg=name)
            self.assertLess(probs[2], mult[2], msg=name)

    def test_no_margin_is_identity(self):
        for method in devig.METHODS:
            probs = devig.devig([2.0, 4.0, 4.0], method)
            self.assertAlmostEqual(probs[0], 0.5, places=6, msg=method)
            self.assertAlmostEqual(probs[1], 0.25, places=6, msg=method)

    def test_rejects_bad_odds(self):
        with self.assertRaises(ValueError):
            devig.devig([1.90, 1.0], "shin")
        with self.assertRaises(ValueError):
            devig.devig([1.90, 3.40], "inventado")


class TestBookProbabilities(unittest.TestCase):
    def test_skips_incomplete_books(self):
        prices = {
            "pinnacle": {"home": 1.90, "draw": 3.40, "away": 4.20},
            "rara": {"home": 2.00},  # falta draw/away
        }
        probs = devig.book_probabilities(prices)
        self.assertEqual(list(probs), ["pinnacle"])

    def test_best_prices(self):
        prices = {
            "a": {"home": 1.90, "away": 4.20},
            "b": {"home": 2.05, "away": 3.90},
        }
        best = devig.best_prices(prices)
        self.assertEqual(best["home"], (2.05, "b"))
        self.assertEqual(best["away"], (4.20, "a"))


class TestArbitrage(unittest.TestCase):
    def test_detects_arbitrage(self):
        # 2.10 + 2.10 => suma de inversos 0.952 < 1 => ~5% de beneficio
        prices = {"a": {"home": 2.10, "away": 1.80}, "b": {"home": 1.80, "away": 2.10}}
        result = devig.arbitrage(prices)
        self.assertIsNotNone(result)
        profit, best = result
        self.assertAlmostEqual(profit, 0.05, places=6)
        self.assertEqual(best["home"], (2.10, "a"))

    def test_no_arbitrage_with_margin(self):
        prices = {"a": {"home": 1.90, "away": 1.90}}
        self.assertIsNone(devig.arbitrage(prices))


class TestConsensus(unittest.TestCase):
    def test_sharp_book_dominates(self):
        probs = {
            "pinnacle": {"home": 0.60, "away": 0.40},
            "casual": {"home": 0.40, "away": 0.60},
        }
        cons = devig.consensus(probs)
        # Pinnacle pesa 1.0 y una casa genérica 0.35 => ~0.548.
        self.assertGreater(cons["home"], 0.54)
        self.assertAlmostEqual(sum(cons.values()), 1.0, places=9)

    def test_ignores_inconsistent_outcomes(self):
        probs = {
            "a": {"home": 0.6, "away": 0.4},
            "b": {"home": 0.5, "away": 0.3, "draw": 0.2},
        }
        cons = devig.consensus(probs)
        self.assertEqual(sorted(cons), ["away", "home"])


if __name__ == "__main__":
    unittest.main()
