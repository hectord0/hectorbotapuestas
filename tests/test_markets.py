import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bettingbot import leagues
from bettingbot.config import Config
from bettingbot.models import CardsModel, CornersModel, counts
from bettingbot.models.football import FootballModel, MatchResult
from bettingbot.models.registry import ModelRegistry
from bettingbot.pipeline import analyse
from bettingbot.providers.manual import ManualProvider
from bettingbot.types import Event, Quote, market_key


def make_event(home="A", away="B", league="Serie B", extra=None):
    return Event(
        id="e1",
        sport="soccer",
        league=league,
        home=home,
        away=away,
        commence=datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc),
        extra=extra or {},
    )


def fitted_football():
    results = []
    for i in range(60):
        results.append(
            MatchResult(
                date=__import__("datetime").date(2025, 1, 1),
                home="A" if i % 2 == 0 else "B",
                away="B" if i % 2 == 0 else "A",
                home_goals=2 if i % 3 else 1,
                away_goals=1 if i % 4 else 0,
            )
        )
    return FootballModel(iterations=200).fit(results)


class TestFootballLinedMarkets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = fitted_football()

    def _probs(self, quotes=()):
        return self.model.probabilities(make_event(), list(quotes))

    def test_totals_at_arbitrary_line(self):
        quotes = [Quote("e1", "x", "totals", "over", 2.0, point=4.5)]
        probs = self._probs(quotes)
        key = market_key("totals", 4.5)
        self.assertIn(key, probs)
        self.assertAlmostEqual(probs[key]["over"] + probs[key]["under"], 1.0, places=9)

    def test_totals_monotonic_in_line(self):
        probs = self._probs(
            [Quote("e1", "x", "totals", "over", 2.0, point=l) for l in (1.5, 2.5, 3.5)]
        )
        overs = [probs[market_key("totals", l)]["over"] for l in (1.5, 2.5, 3.5)]
        self.assertGreater(overs[0], overs[1])
        self.assertGreater(overs[1], overs[2])

    def test_team_totals_home(self):
        quotes = [Quote("e1", "x", "team_totals_home", "over", 2.0, point=1.5)]
        probs = self._probs(quotes)
        key = market_key("team_totals_home", 1.5)
        self.assertIn(key, probs)
        self.assertAlmostEqual(probs[key]["over"] + probs[key]["under"], 1.0, places=9)

    def test_spreads_home_handicap(self):
        quotes = [Quote("e1", "x", "spreads", "home", 2.0, point=-1.5)]
        probs = self._probs(quotes)
        key = market_key("spreads", -1.5)
        self.assertIn(key, probs)
        self.assertAlmostEqual(probs[key]["home"] + probs[key]["away"], 1.0, places=9)
        # Un hándicap más exigente (local -1.5) da menos prob que uno suave.
        probs2 = self._probs([Quote("e1", "x", "spreads", "home", 2.0, point=0.5)])
        self.assertLess(probs[key]["home"], probs2[market_key("spreads", 0.5)]["home"])

    def test_btts_present(self):
        probs = self._probs()
        self.assertIn("btts", probs)
        self.assertAlmostEqual(probs["btts"]["yes"] + probs["btts"]["no"], 1.0, places=9)


class TestCardsModel(unittest.TestCase):
    def test_expected_cards_uses_league_prior(self):
        model = CardsModel()
        la_liga = model.expected_cards(make_event(league="LaLiga"))
        serie_b = model.expected_cards(make_event(league="Serie B"))
        self.assertGreater(la_liga, 0)
        self.assertGreater(serie_b, 0)
        self.assertNotAlmostEqual(la_liga, serie_b)

    def test_derby_and_referee_boost(self):
        model = CardsModel()
        base = model.expected_cards(make_event(league="Serie B"))
        boosted = model.expected_cards(
            make_event(league="Serie B", extra={"derby": True, "referee_factor": 1.3})
        )
        self.assertGreater(boosted, base)

    def test_probabilities_sum_and_monotonic(self):
        model = CardsModel()
        probs = model.probabilities(make_event(league="Serie B"))
        self.assertTrue(probs)
        for key, row in probs.items():
            self.assertAlmostEqual(row["over"] + row["under"], 1.0, places=9)
        overs = [probs[market_key("cards", l)]["over"] for l in (3.5, 4.5, 5.5)]
        self.assertEqual(overs, sorted(overs, reverse=True))

    def test_fit_uses_results(self):
        model = CardsModel()
        results = [
            {"home": "A", "away": "B", "home_cards": 5, "away_cards": 4}
            for _ in range(10)
        ]
        model.fit(results)
        self.assertTrue(model.fitted)
        self.assertGreater(model.league_mean, 8)


class TestCornersModel(unittest.TestCase):
    def test_overdispersion_vs_poisson(self):
        # En la cola alta, la binomial negativa debe dar más masa que Poisson.
        mean, size, line = 10.0, 9.0, 14.5
        nb = counts.over_prob_negbinom(mean, size, line)
        poi = counts.over_prob_poisson(mean, line)
        self.assertGreater(nb, poi)

    def test_probabilities_valid(self):
        model = CornersModel()
        probs = model.probabilities(make_event(league="Serie B"))
        self.assertTrue(probs)
        for row in probs.values():
            self.assertAlmostEqual(row["over"] + row["under"], 1.0, places=9)

    def test_must_win_boost(self):
        model = CornersModel()
        base = model.expected_corners(make_event(league="Serie B"))
        boosted = model.expected_corners(make_event(league="Serie B", extra={"must_win": True}))
        self.assertGreater(boosted, base)

    def test_fit(self):
        model = CornersModel()
        model.fit(
            [{"home": "A", "away": "B", "home_corners": 7, "away_corners": 5} for _ in range(8)]
        )
        self.assertTrue(model.fitted)
        self.assertAlmostEqual(model.league_mean, 12.0)


class TestLeagues(unittest.TestCase):
    def test_find_by_alias_and_accent(self):
        self.assertIsNotNone(leagues.find("LaLiga"))
        self.assertIsNotNone(leagues.find("la liga"))
        self.assertIsNotNone(leagues.find("Serie B"))
        self.assertIsNotNone(leagues.find("Brasileirao Serie A"))

    def test_unknown_returns_none(self):
        self.assertIsNone(leagues.find("Liga Inventada de Marte"))

    def test_prioritized_puts_error_prone_first(self):
        ordered = leagues.prioritized("soccer")
        self.assertGreaterEqual(ordered[0].priority, ordered[-1].priority)
        self.assertEqual(ordered[0].error_risk, "alta")

    def test_filter_keys(self):
        keys = leagues.filter_keys(["LaLiga", "no existe"])
        self.assertEqual(len(keys), 1)

    def test_secondary_league_has_higher_priority(self):
        premier = leagues.find("Premier League")
        serie_b = leagues.find("Serie B")
        self.assertGreater(serie_b.priority, premier.priority)


class TestManualProvider(unittest.TestCase):
    def test_parses_events_and_quotes(self):
        payload = {
            "events": [
                {
                    "id": "m1",
                    "sport": "soccer",
                    "league": "Serie B",
                    "home": "A",
                    "away": "B",
                    "commence": "2026-12-01T20:00:00+00:00",
                    "extra": {"derby": True},
                    "quotes": [
                        {"bookmaker": "x", "market": "cards", "outcome": "over", "point": 4.5, "price": 2.0},
                        {"bookmaker": "x", "market": "h2h", "outcome": "home", "price": 1.9},
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            snap = ManualProvider(path, verbose=False).fetch(["soccer"])
        self.assertEqual(len(snap.events), 1)
        self.assertEqual(snap.events[0].extra, {"derby": True})
        self.assertEqual(len(snap.quotes), 2)
        self.assertEqual(snap.quotes[0].point, 4.5)

    def test_filters_sports_and_skips_bad_quotes(self):
        payload = {
            "events": [
                {
                    "id": "m1", "sport": "tennis", "home": "A", "away": "B",
                    "quotes": [
                        {"bookmaker": "x", "market": "h2h", "outcome": "home", "price": 0.5},
                        {"bookmaker": "x", "market": "h2h", "outcome": "away", "price": 2.0},
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            snap = ManualProvider(path, verbose=False).fetch(["soccer"])
        self.assertEqual(snap.events, [])
        self.assertEqual(snap.quotes, [])

    def test_missing_file_is_empty(self):
        snap = ManualProvider("no-existe-xyz.json", verbose=False).fetch(["soccer"])
        self.assertEqual(snap.events, [])


class TestMultiModelRegistry(unittest.TestCase):
    def test_combines_markets_from_several_models(self):
        registry = ModelRegistry()
        registry.register("soccer", CardsModel())
        registry.register("soccer", CornersModel())
        event = make_event(league="Serie B")
        combined = registry.combined("soccer", event, [])
        self.assertTrue(any(k.startswith("cards") for k in combined))
        self.assertTrue(any(k.startswith("corners") for k in combined))

    def test_first_model_wins_on_collision(self):
        class A:
            name = "a"
            def probabilities(self, event, quotes=None):
                return {"h2h": {"home": 0.6, "away": 0.4}}

        class B:
            name = "b"
            def probabilities(self, event, quotes=None):
                return {"h2h": {"home": 0.1, "away": 0.9}, "x": {"a": 1.0}}

        registry = ModelRegistry()
        registry.register("soccer", A())
        registry.register("soccer", B())
        combined = registry.combined("soccer", make_event(), [])
        self.assertEqual(combined["h2h"]["home"], 0.6)
        self.assertIn("x", combined)

    def test_broken_model_does_not_break_others(self):
        class Broken:
            name = "broken"
            def probabilities(self, event, quotes=None):
                raise RuntimeError("boom")

        registry = ModelRegistry()
        registry.register("soccer", Broken())
        registry.register("soccer", CardsModel())
        combined = registry.combined("soccer", make_event(), [])
        self.assertTrue(any(k.startswith("cards") for k in combined))


class TestOddsApiParsing(unittest.TestCase):
    def setUp(self):
        from bettingbot.providers.oddsapi import OddsApiProvider

        self.provider = OddsApiProvider("dummy-key", verbose=False)
        self.event = make_event(home="Palermo", away="Bari")

    def _raw(self, market_key, outcomes):
        return {
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "last_update": "2026-11-17T12:00:00Z",
                    "markets": [{"key": market_key, "outcomes": outcomes}],
                }
            ]
        }

    def test_spreads_share_home_handicap_key(self):
        raw = self._raw(
            "spreads",
            [
                {"name": "Palermo", "price": 1.95, "point": -1.5},
                {"name": "Bari", "price": 1.90, "point": 1.5},
            ],
        )
        quotes = self.provider._parse_quotes(raw, self.event)
        keys = {q.key for q in quotes}
        self.assertEqual(keys, {"spreads@-1.5"})
        outcomes = {q.outcome: q.point for q in quotes}
        self.assertEqual(outcomes, {"home": -1.5, "away": -1.5})

    def test_btts_mapping(self):
        raw = self._raw(
            "btts",
            [
                {"name": "Yes", "price": 1.80},
                {"name": "No", "price": 2.00},
            ],
        )
        quotes = self.provider._parse_quotes(raw, self.event)
        self.assertEqual({q.outcome for q in quotes}, {"yes", "no"})
        self.assertEqual({q.market for q in quotes}, {"btts"})

    def test_team_totals_split_home_away(self):
        raw = self._raw(
            "team_totals",
            [
                {"name": "Palermo", "description": "Over", "point": 1.5, "price": 2.1},
                {"name": "Palermo", "description": "Under", "point": 1.5, "price": 1.7},
                {"name": "Bari", "description": "Over", "point": 1.5, "price": 2.4},
                {"name": "Bari", "description": "Under", "point": 1.5, "price": 1.5},
            ],
        )
        quotes = self.provider._parse_quotes(raw, self.event)
        markets = {q.market for q in quotes}
        self.assertEqual(markets, {"team_totals_home", "team_totals_away"})
        self.assertEqual(len(quotes), 4)


class TestLeagueSeverityBump(unittest.TestCase):
    def _quotes(self):
        return [
            Quote("e1", "pinnacle", "h2h", "home", 1.95),
            Quote("e1", "pinnacle", "h2h", "away", 1.95),
            Quote("e1", "bet365", "h2h", "home", 2.40),
            Quote("e1", "bet365", "h2h", "away", 1.65),
        ]

    def test_error_prone_league_gets_higher_severity(self):
        cfg = Config()
        cfg.event_day = "all"  # esta prueba no trata del filtro de día
        cfg.error_scan_days = 30  # el fixture está a 13 días
        cfg.single_book_only = False  # aquí se prueba MISPRICING, no el filtro de casa
        registry = ModelRegistry()
        risky = analyse({"e1": make_event(league="Serie B")}, self._quotes(), registry, cfg)
        safe = analyse({"e1": make_event(league="Premier League")}, self._quotes(), registry, cfg)
        risky_sev = max(s.severity for s in risky)
        safe_sev = max(s.severity for s in safe)
        self.assertGreaterEqual(risky_sev, safe_sev)
        self.assertTrue(any("riesgo de error" in s.detail for s in risky))

    def test_leagues_filter_can_exclude_event(self):
        cfg = Config()
        cfg.event_day = "all"
        cfg.leagues = ["LaLiga"]
        signals = analyse({"e1": make_event(league="Serie B")}, self._quotes(), ModelRegistry(), cfg)
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
