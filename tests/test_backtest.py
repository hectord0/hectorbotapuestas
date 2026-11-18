import csv
import math
import random
import tempfile
import unittest
from pathlib import Path

from bettingbot import backtest


def write_csv(path: Path, n: int = 140, seed: int = 3):
    rng = random.Random(seed)
    teams = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]
    rows = []
    day = 1
    for i in range(n):
        h, a = rng.sample(teams, 2)
        lh = 1.5 if h < a else 1.1
        la = 1.1 if h < a else 1.5
        hg = poisson(rng, lh)
        ag = poisson(rng, la)
        # Cuotas coherentes con un modelo Poisson simple + 6% de margen.
        ph = 0.45 if h < a else 0.28
        pd = 0.27
        pa = 1 - ph - pd
        margin = 1.06
        rows.append(
            {
                "Div": "T1",
                "Date": f"{day:02d}/03/2025",
                "HomeTeam": h,
                "AwayTeam": a,
                "FTHG": hg,
                "FTAG": ag,
                "B365H": round(1 / (ph * margin), 2),
                "B365D": round(1 / (pd * margin), 2),
                "B365A": round(1 / (pa * margin), 2),
            }
        )
        day = day % 28 + 1
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def poisson(rng, lam):
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


class TestBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "E0.csv"
        write_csv(cls.path)
        cls.rows = backtest.load_csv(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_loads_rows_and_odds(self):
        self.assertEqual(len(self.rows), 140)
        self.assertTrue(all(r.odds for r in self.rows))
        self.assertTrue(all(len(r.odds) == 3 for r in self.rows))

    def test_sorted_by_date(self):
        self.assertEqual(self.rows, sorted(self.rows, key=lambda r: r.date))

    def test_result_label(self):
        row = backtest.MatchRow(date=None, home="a", away="b", home_goals=2, away_goals=1)
        self.assertEqual(row.result, "home")
        row = backtest.MatchRow(date=None, home="a", away="b", home_goals=0, away_goals=3)
        self.assertEqual(row.result, "away")
        row = backtest.MatchRow(date=None, home="a", away="b", home_goals=1, away_goals=1)
        self.assertEqual(row.result, "draw")

    def test_evaluate_structure(self):
        report = backtest.evaluate(self.rows, min_edge=0.02)
        self.assertEqual(report.n_train + report.n_test, 140)
        self.assertGreater(report.model_logloss, 0)
        self.assertGreater(report.market_logloss, 0)
        self.assertIn("Log-loss modelo", report.summary())

    def test_too_few_rows_raises(self):
        with self.assertRaises(ValueError):
            backtest.evaluate(self.rows[:10])

    def test_max_price_filters_longshots(self):
        loose = backtest.evaluate(self.rows, min_edge=0.0)
        tight = backtest.evaluate(self.rows, min_edge=0.0, max_price=2.0)
        self.assertLessEqual(tight.bets, loose.bets)


if __name__ == "__main__":
    unittest.main()
