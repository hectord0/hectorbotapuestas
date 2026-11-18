import unittest
from datetime import datetime, timedelta, timezone

from bettingbot.analysis import errors, movement, value
from bettingbot.analysis.value import ValueConfig
from bettingbot.types import Event, Quote

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def event():
    return Event(
        id="ev1", sport="soccer", league="LaLiga", home="Madrid", away="Barca",
        commence=datetime(2026, 6, 2, 20, 0, tzinfo=timezone.utc),
    )


def quotes(rows):
    """rows: [(book, market, outcome, price, point)]"""
    return [
        Quote(
            event_id="ev1", bookmaker=b, market=m, outcome=o, price=p,
            point=pt, captured_at=T0,
        )
        for b, m, o, p, pt in rows
    ]


class TestKelly(unittest.TestCase):
    def test_positive_edge_gives_stake(self):
        stake = value.kelly_stake(0.45, 2.50, ValueConfig(bankroll=1000))
        self.assertGreater(stake, 0)

    def test_no_edge_gives_zero(self):
        self.assertEqual(value.kelly_stake(0.40, 2.00, ValueConfig()), 0.0)

    def test_capped_by_max_stake(self):
        cfg = ValueConfig(bankroll=1000, max_stake_pct=0.02, kelly_fraction=1.0)
        self.assertLessEqual(value.kelly_stake(0.9, 5.0, cfg), 20.0)


class TestValueBets(unittest.TestCase):
    def test_detects_value_when_model_above_price(self):
        qs = quotes([("pinnacle", "h2h", "home", 2.50, None)])
        model = {"h2h": {"home": 0.45}}
        sigs = value.find_value_bets(event(), qs, model)
        self.assertEqual(len(sigs), 1)
        self.assertAlmostEqual(sigs[0].edge, 0.125, places=6)
        self.assertGreater(sigs[0].stake, 0)

    def test_ignores_low_edge(self):
        qs = quotes([("pinnacle", "h2h", "home", 2.10, None)])
        sigs = value.find_value_bets(event(), qs, {"h2h": {"home": 0.45}})
        self.assertEqual(sigs, [])  # edge 0.45*2.10-1 = -5.5%

    def test_picks_best_price_across_books(self):
        qs = quotes([
            ("pinnacle", "h2h", "home", 2.40, None),
            ("bet365", "h2h", "home", 2.60, None),
        ])
        sigs = value.find_value_bets(event(), qs, {"h2h": {"home": 0.45}})
        self.assertEqual(sigs[0].bookmaker, "bet365")
        self.assertAlmostEqual(sigs[0].price, 2.60)

    def test_respects_price_bounds(self):
        qs = quotes([("x", "h2h", "home", 1.05, None)])
        sigs = value.find_value_bets(event(), qs, {"h2h": {"home": 0.99}})
        self.assertEqual(sigs, [])

    def test_model_disagreement_guard(self):
        qs = quotes([("pinnacle", "h2h", "home", 4.00, None)])
        sigs = value.find_value_bets(
            event(), qs, {"h2h": {"home": 0.45}},
            sharp_consensus={"h2h": {"home": 0.20}},
        )
        self.assertEqual(sigs, [])

    def test_handles_lined_markets(self):
        qs = quotes([("x", "totals", "over", 2.30, 2.5)])
        sigs = value.find_value_bets(
            event(), qs, {bettingbot_key(): {"over": 0.50}}
        )
        self.assertEqual(len(sigs), 1)


def bettingbot_key():
    from bettingbot.types import market_key

    return market_key("totals", 2.5)


class TestMovement(unittest.TestCase):
    def _series(self, prices):
        return [
            movement.PricePoint(t=T0 + timedelta(minutes=10 * i), price=p)
            for i, p in enumerate(prices)
        ]

    def test_analyse_basic(self):
        mv = movement.analyse(self._series([2.20, 2.10, 1.90]), 90)
        self.assertAlmostEqual(mv.opening, 2.20)
        self.assertAlmostEqual(mv.last, 1.90)
        self.assertLess(mv.change_pct, 0)

    def test_analyse_needs_two_points(self):
        self.assertIsNone(movement.analyse(self._series([2.0]), 90))

    def test_detects_steam(self):
        series = {("ev1", "h2h", "home", "pinnacle"): self._series([2.20, 2.10, 1.95])}
        sigs = movement.detect(series, {"ev1": event()})
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].kind, "STEAM")

    def test_detects_drift(self):
        series = {("ev1", "h2h", "home", "pinnacle"): self._series([1.90, 2.00, 2.15])}
        sigs = movement.detect(series, {"ev1": event()})
        self.assertEqual(sigs[0].kind, "DRIFT")

    def test_ignores_small_changes(self):
        series = {("ev1", "h2h", "home", "pinnacle"): self._series([2.00, 2.01, 2.02])}
        self.assertEqual(movement.detect(series, {"ev1": event()}), [])

    def test_ignores_unknown_event(self):
        series = {("nope", "h2h", "home", "pinnacle"): self._series([2.0, 1.5])}
        self.assertEqual(movement.detect(series, {"ev1": event()}), [])


class TestErrors(unittest.TestCase):
    def test_detects_mispricing(self):
        # Consenso ~ 0.50/0.50, una casa paga 2.40 al home (edge +20%)
        qs = quotes([
            ("pinnacle", "h2h", "home", 1.95, None),
            ("pinnacle", "h2h", "away", 1.95, None),
            ("bet365", "h2h", "home", 2.40, None),
            ("bet365", "h2h", "away", 1.65, None),
        ])
        sigs = errors.detect_errors(event(), qs)
        kinds = {s.kind for s in sigs}
        self.assertIn("MISPRICING", kinds)
        mis = [s for s in sigs if s.kind == "MISPRICING"][0]
        self.assertEqual(mis.bookmaker, "bet365")
        self.assertGreater(mis.edge, 0.05)

    def test_sharp_not_flagged_as_error(self):
        # Pinnacle paga más que bet365 pero sin crear arbitraje.
        qs = quotes([
            ("pinnacle", "h2h", "home", 2.40, None),
            ("pinnacle", "h2h", "away", 1.62, None),
            ("bet365", "h2h", "home", 1.95, None),
            ("bet365", "h2h", "away", 1.95, None),
        ])
        sigs = errors.detect_errors(event(), qs)
        mispricing = [s for s in sigs if s.kind == "MISPRICING"]
        self.assertFalse(any(s.bookmaker == "pinnacle" for s in mispricing))

    def test_detects_arbitrage(self):
        qs = quotes([
            ("a", "h2h", "home", 2.10, None),
            ("a", "h2h", "away", 1.80, None),
            ("b", "h2h", "home", 1.80, None),
            ("b", "h2h", "away", 2.10, None),
        ])
        sigs = errors.detect_errors(event(), qs)
        self.assertTrue(any(s.kind == "ARB" for s in sigs))

    def test_requires_two_books(self):
        qs = quotes([("a", "h2h", "home", 2.00, None), ("a", "h2h", "away", 2.00, None)])
        self.assertEqual(errors.detect_errors(event(), qs), [])

    def test_detects_divergence(self):
        qs = quotes([
            ("a", "h2h", "home", 1.80, None),
            ("a", "h2h", "away", 2.10, None),
            ("b", "h2h", "home", 2.05, None),
            ("b", "h2h", "away", 1.85, None),
        ])
        sigs = errors.detect_errors(event(), qs)
        self.assertTrue(any(s.kind in ("DIVERGENCE", "ARB", "MISPRICING") for s in sigs))


if __name__ == "__main__":
    unittest.main()
