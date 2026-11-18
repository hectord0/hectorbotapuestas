import unittest
from datetime import datetime, timezone

from bettingbot.alerts import (
    ConsoleNotifier,
    DiscordNotifier,
    MultiNotifier,
    NtfyNotifier,
    TelegramNotifier,
    WindowsNotifier,
    build_notifier_for_config,
    format_signal,
    format_signal_plain,
)
from bettingbot.config import Config
from bettingbot.types import Signal


def signal():
    return Signal(
        kind="VALUE",
        sport="soccer",
        league="Serie B",
        match="Palermo vs Bari",
        commence=datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc),
        market="cards@4.5",
        outcome="over",
        bookmaker="bet365",
        price=2.05,
        fair_prob=0.52,
        edge=0.066,
        stake=12.0,
        detail="modelo 52.0% vs cuota 2.05",
        event_id="e1",
    )


class TestSelection(unittest.TestCase):
    def test_defaults_to_console(self):
        self.assertIsInstance(build_notifier_for_config(Config()), ConsoleNotifier)

    def test_force_console_wins(self):
        cfg = Config()
        cfg.ntfy_topic = "algo"
        self.assertIsInstance(build_notifier_for_config(cfg, force_console=True), ConsoleNotifier)

    def test_ntfy(self):
        cfg = Config()
        cfg.ntfy_topic = "hecto-bets-123"
        self.assertIsInstance(build_notifier_for_config(cfg), NtfyNotifier)

    def test_discord(self):
        cfg = Config()
        cfg.discord_webhook_url = "https://discord.com/api/webhooks/x/y"
        self.assertIsInstance(build_notifier_for_config(cfg), DiscordNotifier)

    def test_telegram_takes_priority_in_auto(self):
        cfg = Config()
        cfg.telegram_token = "t"
        cfg.telegram_chat_id = "1"
        cfg.ntfy_topic = "algo"
        self.assertIsInstance(build_notifier_for_config(cfg), TelegramNotifier)

    def test_explicit_notifier(self):
        cfg = Config()
        cfg.notifier = "ntfy"
        cfg.ntfy_topic = "algo"
        self.assertIsInstance(build_notifier_for_config(cfg), NtfyNotifier)

    def test_missing_credentials_falls_back_to_console(self):
        cfg = Config()
        cfg.notifier = "discord"
        self.assertIsInstance(build_notifier_for_config(cfg), ConsoleNotifier)

    def test_unknown_notifier_falls_back(self):
        cfg = Config()
        cfg.notifier = "paloma-mensajera"
        self.assertIsInstance(build_notifier_for_config(cfg), ConsoleNotifier)


class TestFormatting(unittest.TestCase):
    def test_plain_includes_key_data(self):
        text = format_signal_plain(signal())
        self.assertIn("VALOR", text)
        self.assertIn("Palermo vs Bari", text)
        self.assertIn("2.05", text)
        self.assertIn("Más de 4.5 tarjetas", text)
        self.assertIn("Cuota justa", text)
        self.assertIn("DECISIÓN:", text)
        self.assertIn("Motivos del riesgo:", text)

    def test_html_includes_market_and_price(self):
        text = format_signal(signal())
        self.assertIn("<b>2.05</b>", text)
        self.assertIn("Más de 4.5 tarjetas", text)
        self.assertIn("<b>VALOR</b>", text)

    def test_unknown_kind_still_renders(self):
        sig = signal()
        sig = sig.__class__(**{**sig.__dict__, "kind": "RARO"})
        text = format_signal_plain(sig)
        self.assertIn("[RARO]", text)

    def test_includes_date_time_and_sport(self):
        text = format_signal_plain(signal())
        self.assertIn("Cuándo:", text)
        self.assertIn("(hora de España)", text)
        self.assertIn("Fútbol · Serie B", text)
        self.assertIn("Factores:", text)
        self.assertIn("ACCIÓN:", text)

    def test_kickoff_converts_utc_to_spain(self):
        from datetime import datetime, timezone

        from bettingbot.alerts import _kickoff

        # 20:00 UTC en invierno = 21:00 en España
        self.assertIn("21:00", _kickoff(datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc)))
        # 20:00 UTC en verano = 22:00 en España
        self.assertIn("22:00", _kickoff(datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)))
        self.assertEqual(_kickoff(None), "fecha por confirmar")

    def test_event_word_depends_on_sport(self):
        from bettingbot.alerts import _event_word

        self.assertEqual(_event_word("tennis"), "Encuentro")
        self.assertEqual(_event_word("mma"), "Combate")
        self.assertEqual(_event_word("soccer"), "Partido")

    def test_explanation_says_value_is_not_safe(self):
        from bettingbot.alerts import explain

        why, safe = explain(signal())
        self.assertIn("52%", why)
        self.assertIn("2.05", why)
        self.assertIn("NO es segura", safe)

    def test_explanation_for_arbitrage_is_almost_safe(self):
        from bettingbot.alerts import explain

        arb = signal().__class__(**{**signal().__dict__, "kind": "ARB"})
        why, safe = explain(arb)
        self.assertIn("TODOS", why)
        self.assertIn("SÍ es casi segura", safe)

    def test_tennis_has_retirement_warning(self):
        from bettingbot.alerts import explain

        tennis = signal().__class__(**{**signal().__dict__, "sport": "tennis"})
        _, safe = explain(tennis)
        self.assertIn("Tenis", safe)
        self.assertIn("retiradas", safe)

    def test_grave_error_prints_peligro(self):
        grave = signal().__class__(**{
            **signal().__dict__, "kind": "MISPRICING", "severity": 3,
        })
        text = format_signal_plain(grave)
        self.assertIn("PELIGRO: ERROR GRAVE", text)
        self.assertIn("DECISIÓN:", text)

    def test_value_does_not_print_peligro(self):
        value = signal().__class__(**{**signal().__dict__, "severity": 3})
        self.assertNotIn("PELIGRO", format_signal_plain(value))

    def test_no_stake_when_decision_is_not_bet(self):
        text = format_signal_plain(signal())
        self.assertIn("DECISIÓN: NO APOSTAR", text)
        self.assertNotIn("Apuesta:", text)

    def test_exact_bet_for_value(self):
        from bettingbot.alerts import exact_bet
        from bettingbot.safety import assess

        safe = signal().__class__(**{
            **signal().__dict__,
            "market": "totals@2.5", "price": 1.30, "fair_prob": 0.82,
            "edge": 0.066, "stake": 12.5, "sport": "soccer",
        })
        text = exact_bet(safe, assess(safe))
        self.assertIn("Más de 2.5 goles", text)
        self.assertIn("a 1.30 en bet365", text)
        self.assertIn("12.50 €", text)

    def test_exact_bet_for_arbitrage_lists_every_bookmaker(self):
        from bettingbot.alerts import exact_bet
        from bettingbot.safety import assess

        arb = signal().__class__(**{
            **signal().__dict__,
            "kind": "ARB", "market": "corners@10.5",
            "detail": "arbitraje +8.89%: over 2.25 en betfair, under 2.11 en bet365",
        })
        text = exact_bet(arb, assess(arb))
        self.assertIn("Más de 10.5 córners @2.25 en betfair", text)
        self.assertIn("Menos de 10.5 córners @2.11 en bet365", text)
        self.assertIn("beneficio", text)

    def test_exact_bet_says_none_when_not_betting(self):
        from bettingbot.alerts import exact_bet
        from bettingbot.safety import assess

        self.assertIn("NINGUNA", exact_bet(signal(), assess(signal())))

    def test_brief_format_is_one_line(self):
        from bettingbot.alerts import format_brief

        text = format_brief(signal())
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn("NO APOSTAR", text)
        self.assertIn("sin apuesta recomendada", text)


class TestDiscordChunks(unittest.TestCase):
    def test_short_message_is_one_chunk(self):
        self.assertEqual(DiscordNotifier._chunks("hola"), ["hola"])

    def test_long_message_is_split_under_limit(self):
        text = "\n".join(f"linea {i} " + "x" * 100 for i in range(60))
        chunks = DiscordNotifier._chunks(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), DiscordNotifier.MAX_CHARS)
        self.assertEqual("".join(chunks), text)

    def test_requires_webhook(self):
        with self.assertRaises(ValueError):
            DiscordNotifier("")


class TestNtfy(unittest.TestCase):
    def test_requires_topic(self):
        with self.assertRaises(ValueError):
            NtfyNotifier("")
    def test_normalizes_topic(self):
        n = NtfyNotifier("/mi-tema/")
        self.assertEqual(n.topic, "mi-tema")
        self.assertEqual(n.server, "https://ntfy.sh")

    def test_custom_server(self):
        n = NtfyNotifier("t", server="https://ntfy.midominio.com/")
        self.assertEqual(n.server, "https://ntfy.midominio.com")


class TestMultiChannel(unittest.TestCase):
    def test_requires_at_least_one_channel(self):
        with self.assertRaises(ValueError):
            MultiNotifier([])

    def test_sends_to_every_channel(self):
        class Recorder:
            name = "rec"

            def __init__(self) -> None:
                self.sent = []

            def send(self, text):
                self.sent.append(text)
                return True

            def format(self, signal):
                return "x"

        a, b = Recorder(), Recorder()
        multi = MultiNotifier([a, b])
        self.assertEqual(multi.name, "rec+rec")
        self.assertTrue(multi.send("hola"))
        self.assertEqual(a.sent, ["hola"])
        self.assertEqual(b.sent, ["hola"])

    def test_partial_failure_reports_false(self):
        class Bad:
            name = "bad"

            def send(self, text):
                return False

            def format(self, signal):
                return "x"

        multi = MultiNotifier([ConsoleNotifier(), Bad()])
        self.assertFalse(multi.send("hola"))

    def test_config_accepts_comma_separated_channels(self):
        cfg = Config(notifier="windows,ntfy", ntfy_topic="t")
        notifier = build_notifier_for_config(cfg)
        self.assertIsInstance(notifier, MultiNotifier)
        names = [ch.name for ch in notifier.channels]
        self.assertEqual(names, ["windows", "ntfy"])
        self.assertIsInstance(notifier.channels[0], WindowsNotifier)

    def test_config_single_channel_stays_plain(self):
        cfg = Config(notifier="windows")
        self.assertIsInstance(build_notifier_for_config(cfg), WindowsNotifier)

    def test_windows_channel_is_skipped_outside_windows(self):
        from unittest.mock import patch

        cfg = Config(notifier="windows,ntfy", ntfy_topic="topic")
        with patch("bettingbot.alerts.os.name", "posix"):
            notifier = build_notifier_for_config(cfg)
        self.assertIsInstance(notifier, NtfyNotifier)
        self.assertEqual(notifier.name, "ntfy")


if __name__ == "__main__":
    unittest.main()
