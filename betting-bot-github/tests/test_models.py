import unittest
from datetime import datetime, timezone

from bettingbot.models import (
    BasketballModel,
    Elo,
    MMAModel,
    ModelRegistry,
    TableTennisModel,
    TennisModel,
    TotalPointsModel,
)
from bettingbot.types import Event


def ev(home, away, sport="test", extra=None):
    return Event(
        id="x",
        sport=sport,
        league="L",
        home=home,
        away=away,
        commence=datetime(2026, 1, 1, tzinfo=timezone.utc),
        extra=extra or {},
    )


class TestElo(unittest.TestCase):
    def test_equal_teams(self):
        elo = Elo()
        self.assertAlmostEqual(elo.win_prob("a", "b"), 0.5)

    def test_home_advantage(self):
        elo = Elo(home_advantage=100)
        self.assertGreater(elo.win_prob("a", "b"), 0.5)

    def test_winner_gains_rating(self):
        elo = Elo(k=20)
        elo.update("a", "b", 2, 0)
        self.assertGreater(elo.rating("a"), elo.rating("b"))

    def test_upset_moves_more(self):
        elo = Elo(k=20)
        elo.ratings.update({"a": 1700, "b": 1500})
        before = elo.rating("b")
        elo.update("b", "a", 1, 0)  # b gana siendo peor
        self.assertGreater(elo.rating("b") - before, 10)

    def test_mov_changes_magnitude(self):
        plain, mov = Elo(k=20), Elo(k=20, mov=True)
        plain.update("a", "b", 5, 0)
        mov.update("a", "b", 5, 0)
        self.assertNotAlmostEqual(plain.rating("a"), mov.rating("a"))


class TestBasketball(unittest.TestCase):
    def setUp(self):
        self.model = BasketballModel()
        results = []
        for _ in range(6):
            results.append({"home": "LAL", "away": "BOS", "home_score": 112, "away_score": 100})
            results.append({"home": "BOS", "away": "LAL", "home_score": 108, "away_score": 105})
        self.model.fit(results)

    def test_h2h_sums_to_one(self):
        p = self.model.probabilities(ev("LAL", "BOS"))["h2h"]
        self.assertAlmostEqual(p["home"] + p["away"], 1.0, places=9)

    def test_home_team_that_wins_more_is_favourite(self):
        p = self.model.probabilities(ev("LAL", "BOS"))["h2h"]
        self.assertGreater(p["home"], 0.5)

    def test_totals_published_and_valid(self):
        probs = self.model.probabilities(ev("LAL", "BOS"))
        totals = [k for k in probs if k.startswith("totals")]
        self.assertTrue(totals)
        for k in totals:
            self.assertAlmostEqual(probs[k]["over"] + probs[k]["under"], 1.0, places=9)

    def test_expected_total_near_observed(self):
        total = self.model.totals.expected_total("LAL", "BOS")
        self.assertGreater(total, 190)
        self.assertLess(total, 240)


class TestTotalPointsModel(unittest.TestCase):
    def test_over_prob_decreasing_in_line(self):
        m = TotalPointsModel(sigma=18)
        m.fit([{"home": "a", "away": "b", "home_score": 110, "away_score": 105}])
        self.assertGreater(m.over_prob("a", "b", 200.5), m.over_prob("a", "b", 220.5))


class TestTennis(unittest.TestCase):
    def test_surface_blend(self):
        model = TennisModel()
        results = []
        for _ in range(10):
            results.append({"home": "Nadal", "away": "Federer", "home_score": 2, "away_score": 0, "surface": "clay"})
            results.append({"home": "Federer", "away": "Nadal", "home_score": 2, "away_score": 1, "surface": "grass"})
        model.fit(results)
        clay = model.win_prob("Nadal", "Federer", "clay")
        grass = model.win_prob("Nadal", "Federer", "grass")
        self.assertGreater(clay, grass)

    def test_point_model_available(self):
        p = TennisModel.point_model_prob(0.65, 0.60, best_of=3)
        self.assertGreater(p, 0.5)


class TestMMA(unittest.TestCase):
    def test_elo_only(self):
        model = MMAModel()
        self.assertAlmostEqual(model.win_prob("a", "b"), 0.5)

    def test_age_penalty(self):
        model = MMAModel()
        old = {"age": 39}
        young = {"age": 26}
        p = model.probabilities(ev("a", "b", extra={"fighter_a": old, "fighter_b": young}))["h2h"]
        self.assertLess(p["home"], 0.5)

    def test_reach_advantage(self):
        model = MMAModel()
        a = {"reach_cm": 195}
        b = {"reach_cm": 180}
        p = model.probabilities(ev("a", "b", extra={"fighter_a": a, "fighter_b": b}))["h2h"]
        self.assertGreater(p["home"], 0.5)


class TestTableTennis(unittest.TestCase):
    def test_implied_point_edge_roundtrip(self):
        target = 0.75
        edge = TableTennisModel.implied_point_edge(target, best_of=5)
        recomputed = __import__(
            "bettingbot.models.pointmodel", fromlist=["table_tennis_match_prob"]
        ).table_tennis_match_prob(0.5 + edge, 0.5, 5)
        self.assertAlmostEqual(recomputed, target, places=6)

    def test_probabilities(self):
        model = TableTennisModel().fit(
            [{"home": "a", "away": "b", "home_score": 3, "away_score": 0}]
        )
        p = model.probabilities(ev("a", "b"))["h2h"]
        self.assertAlmostEqual(p["home"] + p["away"], 1.0, places=9)
        self.assertGreater(p["home"], 0.5)


class TestRegistry(unittest.TestCase):
    def test_register_and_get(self):
        reg = ModelRegistry()
        self.assertIsNone(reg.get("soccer"))
        reg.register("soccer", object())
        self.assertTrue(reg.has("soccer"))
        self.assertEqual(reg.sports(), ["soccer"])


if __name__ == "__main__":
    unittest.main()
