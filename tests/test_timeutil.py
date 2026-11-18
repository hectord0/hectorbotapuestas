import unittest
from datetime import datetime, timedelta, timezone

from bettingbot.timeutil import format_kickoff, in_day, spain_today, to_spain


class TestSpainTime(unittest.TestCase):
    def test_winter_is_plus_one(self):
        dt = to_spain(datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(dt, datetime(2026, 12, 1, 21, 0))

    def test_summer_is_plus_two(self):
        dt = to_spain(datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(dt, datetime(2026, 7, 1, 22, 0))

    def test_naive_is_treated_as_utc(self):
        self.assertEqual(
            to_spain(datetime(2026, 12, 1, 20, 0)),
            datetime(2026, 12, 1, 21, 0),
        )

    def test_invalid_returns_none(self):
        self.assertIsNone(to_spain(None))

    def test_format(self):
        text = format_kickoff(datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc))
        self.assertIn("21:00", text)
        self.assertIn("hora de España", text)
        self.assertEqual(format_kickoff(None), "fecha por confirmar")


class TestDayWindow(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)

    def test_all_accepts_everything(self):
        self.assertTrue(in_day(datetime(2030, 1, 1, tzinfo=timezone.utc), "all", self.now))

    def test_next24h(self):
        self.assertTrue(in_day(self.now + timedelta(hours=2), "next24h", self.now))
        self.assertFalse(in_day(self.now + timedelta(hours=30), "next24h", self.now))
        self.assertFalse(in_day(self.now - timedelta(hours=1), "next24h", self.now))

    def test_hours_n(self):
        self.assertTrue(in_day(self.now + timedelta(hours=6), "hours:12", self.now))
        self.assertFalse(in_day(self.now + timedelta(hours=18), "hours:12", self.now))

    def test_today_uses_spain_calendar(self):
        local = to_spain(self.now).date()
        self.assertTrue(in_day(datetime.combine(local, datetime.min.time()), "today", self.now))
        self.assertFalse(in_day(self.now + timedelta(days=3), "today", self.now))

    def test_tomorrow(self):
        self.assertTrue(in_day(self.now + timedelta(days=1), "tomorrow", self.now))
        self.assertFalse(in_day(self.now, "tomorrow", self.now))

    def test_days_range_is_clamped_to_1_3(self):
        self.assertTrue(in_day(self.now + timedelta(days=2), "days:2", self.now))
        self.assertTrue(in_day(self.now + timedelta(days=3), "days:3", self.now))
        self.assertFalse(in_day(self.now + timedelta(days=4), "days:3", self.now))
        # Fuera de rango se limita: days:9 equivale a days:3.
        self.assertFalse(in_day(self.now + timedelta(days=5), "days:9", self.now))
        self.assertTrue(in_day(self.now + timedelta(days=1), "days:0", self.now))

    def test_in_days_helper(self):
        from bettingbot.timeutil import in_days

        self.assertTrue(in_days(self.now + timedelta(days=7), 7, self.now))
        self.assertFalse(in_days(self.now + timedelta(days=7, hours=1), 7, self.now))

    def test_missing_date_is_rejected(self):
        self.assertFalse(in_day(None, "next24h", self.now))
        self.assertTrue(spain_today() is not None)


if __name__ == "__main__":
    unittest.main()
