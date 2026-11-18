import unittest
from datetime import datetime, timezone

from bettingbot.alerts import _selection_label
from bettingbot.models.football import FootballModel
from bettingbot.models.tennis import (
    TennisModel,
    at_least_one_set_prob,
    set_win_prob,
)
from bettingbot.providers.oddsapi import OddsApiProvider
from bettingbot.providers.sample import SampleProvider
from bettingbot.types import Event


def tennis_event():
    return Event(
        id="t1", sport="tennis", league="ATP", home="Alcaraz", away="Sinner",
        commence=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )


class TestHalfTime(unittest.TestCase):
    def test_half_time_probs_sum_to_one(self):
        probs = FootballModel._half_time_probs(1.5, 1.2)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=6)
        self.assertGreater(probs["home"], probs["away"])

    def test_sample_quotes_first_half(self):
        snap = SampleProvider(steps=1).fetch(["soccer"])
        markets = {q.market for q in snap.quotes}
        self.assertIn("h2h_ht", markets)

    def test_label(self):
        self.assertEqual(
            _selection_label("h2h_ht", "home", "soccer"), "1ª parte: Gana local"
        )

    def test_prior_model_without_history(self):
        from bettingbot.models.football import HalfTimeModel

        event = Event(
            id="s1", sport="soccer", league="Serie B", home="Palermo", away="Bari",
            commence=datetime(2026, 12, 1, tzinfo=timezone.utc),
        )
        probs = HalfTimeModel().probabilities(event)
        self.assertIn("h2h_ht", probs)
        self.assertAlmostEqual(sum(probs["h2h_ht"].values()), 1.0, places=6)


class TestSets(unittest.TestCase):
    def test_set_prob_is_between_match_and_one(self):
        self.assertGreater(set_win_prob(0.70), 0.50)
        self.assertLess(set_win_prob(0.70), 0.70)

    def test_at_least_one_set_exceeds_match_prob(self):
        self.assertGreater(at_least_one_set_prob(0.70), 0.70)
        self.assertLess(at_least_one_set_prob(0.70), 1.0)

    def test_tennis_model_produces_sets_markets(self):
        probs = TennisModel().probabilities(tennis_event())
        self.assertIn("sets_home@0.5", probs)
        self.assertIn("sets_away@0.5", probs)
        self.assertAlmostEqual(
            sum(probs["sets_home@0.5"].values()), 1.0, places=6
        )

    def test_sample_quotes_sets(self):
        snap = SampleProvider(steps=1).fetch(["tennis"])
        markets = {q.market for q in snap.quotes}
        self.assertIn("sets_home", markets)
        self.assertIn("sets_away", markets)

    def test_labels(self):
        self.assertEqual(
            _selection_label("sets_home@0.5", "over", "tennis"),
            "Local gana al menos 1 set",
        )
        self.assertEqual(
            _selection_label("sets_away@0.5", "under", "tennis"),
            "Visitante pierde todos los sets",
        )


class TestOddsApiParsing(unittest.TestCase):
    def test_first_half_key_is_normalized(self):
        raw = {
            "id": "x", "home_team": "A", "away_team": "B",
            "sport_title": "Liga", "commence_time": "2026-12-01T20:00:00Z",
            "bookmakers": [{
                "key": "bet365", "last_update": "2026-12-01T19:00:00Z",
                "markets": [{
                    "key": "h2h_1h",
                    "outcomes": [
                        {"name": "A", "price": 2.10},
                        {"name": "Draw", "price": 3.20},
                        {"name": "B", "price": 3.50},
                    ],
                }],
            }],
        }
        event = Event(
            id="x", sport="soccer", league="Liga", home="A", away="B",
            commence=datetime(2026, 12, 1, tzinfo=timezone.utc),
        )
        quotes = OddsApiProvider("fake")._parse_quotes(raw, event)
        self.assertTrue(quotes)
        self.assertTrue(all(q.market == "h2h_ht" for q in quotes))
        self.assertEqual({q.outcome for q in quotes}, {"home", "draw", "away"})


if __name__ == "__main__":
    unittest.main()
