import unittest
from datetime import datetime, timezone

from bettingbot import factors, leagues
from bettingbot.types import Event, Signal


def event(sport="soccer", league="Serie B"):
    return Event(
        id="e1", sport=sport, league=league,
        home="Palermo", away="Bari",
        commence=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )


def signal(**overrides):
    base = dict(
        kind="VALUE", sport="soccer", league="Serie B", match="Palermo vs Bari",
        commence=datetime(2026, 12, 1, tzinfo=timezone.utc),
        market="totals@2.5", outcome="over", bookmaker="bet365", price=2.1,
    )
    base.update(overrides)
    return Signal(**base)


class TestFactors(unittest.TestCase):
    def test_soccer_includes_home_away_and_league_averages(self):
        text = factors.event_summary(event(), leagues.find("Serie B"))
        self.assertIn("Palermo en casa", text)
        self.assertIn("media", text)
        self.assertIn("tarjetas", text)

    def test_tennis_warns_about_missing_player_data(self):
        text = factors.event_summary(event("tennis", "ATP"))
        self.assertIn("superficie", text)
        self.assertIn("retiradas", text)

    def test_line_vs_league_average(self):
        text = factors.describe(signal())
        self.assertIn("línea 2.5", text)
        self.assertIn("media de liga", text)

    def test_cards_line_uses_cards_average(self):
        text = factors.describe(signal(market="cards@4.5"))
        self.assertIn("tarjetas", text)

    def test_explicit_factors_are_kept(self):
        sig = signal(factors="lesión del delantero titular")
        self.assertIn("lesión del delantero titular", factors.describe(sig))

    def test_no_data_is_explicit(self):
        sig = signal(sport="mma", league="Liga Desconocida", market="h2h")
        self.assertIn("sin factores adicionales", factors.describe(sig))


if __name__ == "__main__":
    unittest.main()
