import math
import random
import unittest
from datetime import date, timedelta

from bettingbot.models.football import FootballModel, MatchResult
from bettingbot.types import Event
from datetime import datetime, timezone


def synthetic_league(seed: int = 7, n_teams: int = 6, rounds: int = 40):
    """Genera partidos desde parámetros conocidos para validar el ajuste."""
    rng = random.Random(seed)
    teams = [f"T{i}" for i in range(n_teams)]
    true_att = {t: rng.gauss(0, 0.35) for t in teams}
    true_def = {t: rng.gauss(0, 0.25) for t in teams}
    mu, home_adv = math.log(1.35), 0.25
    start = date(2025, 1, 1)
    results = []
    day = 0
    for _ in range(rounds):
        for i, h in enumerate(teams):
            for a in teams:
                if a == h:
                    continue
                lam_h = math.exp(mu + true_att[h] - true_def[a] + home_adv)
                lam_a = math.exp(mu + true_att[a] - true_def[h])
                hg = poisson_sample(rng, lam_h)
                ag = poisson_sample(rng, lam_a)
                results.append(
                    MatchResult(
                        date=start + timedelta(days=day),
                        home=h,
                        away=a,
                        home_goals=hg,
                        away_goals=ag,
                    )
                )
                day += 1
    return results, true_att, true_def


def poisson_sample(rng: random.Random, lam: float) -> int:
    # Knuth
    l = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def make_event(home: str, away: str) -> Event:
    return Event(
        id="e1",
        sport="soccer",
        league="Test",
        home=home,
        away=away,
        commence=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestFootballFit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results, cls.true_att, cls.true_def = synthetic_league()
        cls.model = FootballModel().fit(cls.results)

    def test_probabilities_sum_to_one(self):
        probs = self.model.probabilities(make_event("T0", "T1"))
        for market, outcomes in probs.items():
            self.assertAlmostEqual(sum(outcomes.values()), 1.0, places=6, msg=market)

    def test_recovers_team_strength_order(self):
        # El equipo con mayor ataque real debe quedar arriba en el modelo.
        best_team = max(self.true_att, key=self.true_att.get)
        ranked = sorted(self.model.attack.items(), key=lambda kv: kv[1], reverse=True)
        self.assertEqual(ranked[0][0], best_team)

    def test_home_advantage_is_positive(self):
        self.assertGreater(self.model.home_adv, 0.0)

    def test_stronger_team_is_favourite(self):
        best = max(self.true_att, key=self.true_att.get)
        worst = min(self.true_att, key=self.true_att.get)
        p = self.model.probabilities(make_event(best, worst))["h2h"]
        self.assertGreater(p["home"], p["away"])
        self.assertGreater(p["home"], 0.5)

    def test_expected_goals_reasonable(self):
        lh, la = self.model.expected_goals("T0", "T1")
        self.assertGreater(lh, 0.2)
        self.assertLess(lh, 5.0)
        self.assertGreater(la, 0.2)
        self.assertLess(la, 5.0)

    def test_score_matrix_normalized(self):
        m = self.model.score_matrix("T2", "T3")
        total = sum(sum(row) for row in m)
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_rho_in_sane_range(self):
        self.assertGreater(self.model.rho, -0.2)
        self.assertLess(self.model.rho, 0.2)

    def test_top_scores_descending(self):
        scores = self.model.top_scores("T0", "T1", 5)
        probs = [p for _, p in scores]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_unfitted_raises(self):
        with self.assertRaises(RuntimeError):
            FootballModel().probabilities(make_event("A", "B"))


if __name__ == "__main__":
    unittest.main()
