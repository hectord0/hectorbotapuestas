import unittest
from datetime import datetime, timezone

from bettingbot.safety import assess
from bettingbot.types import Signal


def sig(**overrides):
    base = dict(
        kind="VALUE",
        sport="soccer",
        league="Championship",
        match="A vs B",
        commence=datetime(2026, 12, 1, tzinfo=timezone.utc),
        market="h2h",
        outcome="home",
        bookmaker="bet365",
        price=1.25,
        fair_prob=0.80,
        edge=0.05,
        stake=10.0,
        detail="modelo 80%",
    )
    base.update(overrides)
    return Signal(**base)


class TestSafety(unittest.TestCase):
    def test_arbitrage_is_safe_and_bet(self):
        result = assess(sig(kind="ARB", fair_prob=None, price=2.1, detail="arbitraje"))
        self.assertEqual(result.decision, "APOSTAR")
        self.assertEqual(result.level, "BAJO")
        self.assertGreaterEqual(result.score, 85)

    def test_high_probability_value_is_bet(self):
        result = assess(sig())
        self.assertEqual(result.decision, "APOSTAR")
        self.assertEqual(result.level, "BAJO")
        self.assertTrue(result.is_safe)

    def test_low_probability_value_is_not_bet(self):
        result = assess(sig(price=3.0, fair_prob=0.40))
        self.assertEqual(result.level, "ALTO")
        self.assertEqual(result.decision, "NO APOSTAR")

    def test_medium_probability_with_small_edge_is_wait(self):
        result = assess(sig(price=1.95, fair_prob=0.58, edge=0.02))
        self.assertEqual(result.decision, "ESPERAR")

    def test_moderate_risk_with_edge_is_allowed(self):
        result = assess(sig(price=1.95, fair_prob=0.58, edge=0.08))
        self.assertEqual(result.level, "MEDIO")
        self.assertEqual(result.decision, "APOSTAR")
        self.assertTrue(any("stake reducido" in r for r in result.reasons))

    def test_tennis_is_penalized(self):
        soccer = assess(sig())
        tennis = assess(sig(sport="tennis"))
        self.assertLess(tennis.score, soccer.score)
        self.assertTrue(any("riesgo propio del deporte" in r for r in tennis.reasons))

    def test_grave_error_is_not_bet(self):
        result = assess(
            sig(kind="MISPRICING", detail="cuota rara · ERROR GRAVE (revisar)", severity=3)
        )
        self.assertEqual(result.decision, "NO APOSTAR")
        self.assertEqual(result.level, "ALTO")

    def test_informational_signals_are_not_bets(self):
        for kind in ("MISPRICING", "DIVERGENCE", "STEAM", "DRIFT"):
            with self.subTest(kind=kind):
                result = assess(sig(kind=kind, fair_prob=None, severity=2))
                self.assertNotEqual(result.decision, "APOSTAR")


if __name__ == "__main__":
    unittest.main()
