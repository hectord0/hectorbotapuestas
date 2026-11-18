import unittest
from dataclasses import replace
from datetime import timedelta

from bettingbot.alerts import NullNotifier
from bettingbot.config import Config, load_config
from bettingbot.models.registry import ModelRegistry
from bettingbot.pipeline import analyse, run_once, sharp_consensus
from bettingbot.providers.sample import SampleProvider, SampleTruthModel
from bettingbot.safety import assess
from bettingbot.storage import Storage
from bettingbot.types import utcnow


class RecordingNotifier:
    name = "recording"

    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return True

    def format(self, signal):
        return f"{signal.kind}:{signal.match}"


def make_stack(steps=3):
    cfg = Config()
    cfg.alert_cooldown_min = 0
    provider = SampleProvider(steps=steps)
    registry = ModelRegistry()
    truth = SampleTruthModel(provider)
    for sport in cfg.sports:
        registry.register(sport, truth)
    storage = Storage(":memory:")
    return cfg, provider, registry, storage


class StackMixin:
    def stack(self, steps=3):
        cfg, provider, registry, storage = make_stack(steps)
        self.addCleanup(storage.close)
        return cfg, provider, registry, storage


class TestStorage(StackMixin, unittest.TestCase):
    def test_events_and_quotes_roundtrip(self):
        _, provider, _, storage = self.stack()
        snap = provider.fetch(["soccer"])
        storage.upsert_events(snap.events)
        storage.add_quotes(snap.quotes)
        self.assertEqual(storage.event_count(), len(snap.events))
        self.assertEqual(storage.quote_count(), len(snap.quotes))
        events = storage.get_events()
        self.assertEqual(set(events), {e.id for e in snap.events})

    def test_price_history_keys(self):
        _, provider, _, storage = self.stack()
        snap = provider.fetch(["soccer"])
        storage.upsert_events(snap.events)
        storage.add_quotes(snap.quotes)
        series = storage.price_history(since=utcnow() - timedelta(days=1))
        self.assertTrue(series)
        key = next(iter(series))
        self.assertEqual(len(key), 4)  # event, market, outcome, bookmaker

    def test_alert_cooldown(self):
        storage = Storage(":memory:")
        self.addCleanup(storage.close)
        self.assertTrue(storage.should_alert("k", 60))
        from bettingbot.types import Signal

        sig = Signal(
            kind="VALUE", sport="soccer", league="", match="A vs B", commence=None,
            market="h2h", outcome="home", bookmaker="x", price=2.0, event_id="e",
        )
        storage.log_alert(sig, "test", True)
        self.assertFalse(storage.should_alert(sig.dedupe_key, 60))
        self.assertTrue(storage.should_alert(sig.dedupe_key, 0))


class TestPipeline(StackMixin, unittest.TestCase):
    def test_run_once_produces_signals_and_alerts(self):
        cfg, provider, registry, storage = self.stack()
        notifier = RecordingNotifier()
        result = run_once(provider, storage, registry, notifier, cfg)
        self.assertEqual(result.events, 11)
        self.assertGreater(result.quotes, 100)
        self.assertTrue(result.signals)
        self.assertEqual(result.sent, len(result.signals))
        self.assertEqual(len(notifier.messages), result.sent)
        self.assertGreater(storage.alert_count(), 0)

    def test_day_window_filters_far_future_events(self):
        cfg, provider, registry, _ = self.stack()
        cfg.event_day = "next24h"
        cfg.single_book_only = False  # esta prueba es de ventanas, no de casas
        snap = provider.fetch(["soccer"])
        near_id = snap.events[0].id
        events = {
            e.id: replace(
                e,
                commence=(
                    utcnow() + timedelta(hours=2)
                    if e.id == near_id
                    else utcnow() + timedelta(days=5)
                ),
            )
            for e in snap.events
        }
        signals = analyse(events, snap.quotes, registry, cfg)
        self.assertTrue(signals)
        near = [s for s in signals if s.event_id == near_id]
        far = [s for s in signals if s.event_id != near_id]
        self.assertTrue(near)
        # Los partidos lejos del rango solo pueden generar errores graves.
        self.assertTrue(all(s.kind in {"ARB", "MISPRICING", "DIVERGENCE"} for s in far))

    def test_errors_are_scanned_up_to_seven_days(self):
        cfg, provider, registry, _ = self.stack()
        cfg.event_day = "days:1"
        cfg.error_scan_days = 7
        cfg.single_book_only = False  # se prueban los errores explícitamente
        snap = provider.fetch(["soccer"])
        near_id = snap.events[0].id
        week_id = "sample-soccer-2"
        beyond_id = "sample-soccer-1"
        offsets = {near_id: timedelta(hours=2), week_id: timedelta(days=5),
                   beyond_id: timedelta(days=9)}
        events = {
            e.id: replace(e, commence=utcnow() + offsets.get(e.id, timedelta(hours=2)))
            for e in snap.events
        }
        signals = analyse(events, snap.quotes, registry, cfg)
        week_errors = [
            s for s in signals if s.event_id == week_id
            and s.kind in {"ARB", "MISPRICING", "DIVERGENCE"}
        ]
        self.assertTrue(week_errors)
        self.assertFalse([s for s in signals if s.event_id == beyond_id])
        self.assertTrue(any("ERROR GRAVE" in s.detail for s in week_errors))

    def test_max_risk_bajo_filters_moderate_bets(self):
        cfg, provider, registry, _ = self.stack()
        cfg.max_risk_level = "BAJO"
        snap = provider.fetch(["soccer"])
        signals = analyse(
            {e.id: e for e in snap.events}, snap.quotes, registry, cfg
        )
        self.assertTrue(all(s.kind != "ARB" for s in signals))
        self.assertTrue(all(assess(s).level == "BAJO" for s in signals))

    def test_single_book_mode_converts_errors_and_drops_arbitrage(self):
        cfg, provider, registry, _ = self.stack()
        cfg.safety_mode = "all"
        cfg.event_day = "all"
        cfg.error_scan_days = 30
        snap = provider.fetch(["soccer"])
        signals = analyse(
            {e.id: e for e in snap.events}, snap.quotes, ModelRegistry(), cfg
        )
        self.assertFalse([s for s in signals if s.kind == "ARB"])
        converted = [s for s in signals if s.single_book]
        self.assertTrue(converted)
        self.assertTrue(all(s.kind == "VALUE" and s.stake > 0 for s in converted))

    def test_safe_mode_keeps_single_book_bets(self):
        cfg, provider, registry, storage = self.stack()
        result = run_once(provider, storage, registry, NullNotifier(), cfg)
        self.assertTrue(result.signals)
        self.assertTrue(all(s.kind != "ARB" for s in result.signals))
        self.assertEqual(result.sent, len(result.signals))

    def test_notify_window_can_suppress_alerts(self):
        cfg, provider, registry, storage = self.stack()
        cfg.notify_day = "hours:1"  # los partidos del fixture están a +6 h
        notifier = RecordingNotifier()
        result = run_once(provider, storage, registry, notifier, cfg)
        self.assertTrue(result.signals)
        self.assertEqual(result.sent, 0)
        self.assertEqual(notifier.messages, [])

    def test_focus_skips_non_actionable_signals(self):
        cfg, provider, registry, storage = self.stack()
        cfg.safety_mode = "all"
        cfg.focus_actionable = True
        notifier = RecordingNotifier()
        result = run_once(provider, storage, registry, notifier, cfg)
        self.assertGreater(len(result.signals), result.sent)
        self.assertGreater(result.sent, 0)

    def test_value_edge_above_threshold(self):
        cfg, provider, registry, storage = self.stack()
        cfg.safety_mode = "all"  # prueba de la lógica de value, no del filtro safe
        result = run_once(provider, storage, registry, NullNotifier(), cfg)
        values = [s for s in result.signals if s.kind == "VALUE"]
        self.assertTrue(values)
        for s in values:
            self.assertGreaterEqual(s.edge, cfg.value.min_edge)
            self.assertGreater(s.stake, 0)

    def test_movement_detected_after_several_polls(self):
        cfg, provider, registry, storage = self.stack(steps=3)
        cfg.safety_mode = "all"  # aquí se prueba el detector de movimientos
        cfg.focus_actionable = False  # queremos ver esas señales informativas
        notifier = NullNotifier()
        for _ in range(3):
            run_once(provider, storage, registry, notifier, cfg)
        kinds = storage.stats_by_kind()
        self.assertIn("STEAM", kinds)

    def test_cooldown_suppresses_repeats(self):
        cfg, provider, registry, storage = self.stack(steps=3)
        cfg.alert_cooldown_min = 600
        notifier = RecordingNotifier()
        first = run_once(provider, storage, registry, notifier, cfg)
        second = run_once(provider, storage, registry, notifier, cfg)
        self.assertGreater(first.sent, 0)
        self.assertEqual(second.sent, 0)

    def test_mispricing_suppressed_when_value_exists(self):
        cfg, provider, registry, storage = self.stack()
        result = run_once(provider, storage, registry, NullNotifier(), cfg)
        value_sel = {(s.event_id, s.market, s.outcome) for s in result.signals if s.kind == "VALUE"}
        for s in result.signals:
            if s.kind == "MISPRICING":
                self.assertNotIn((s.event_id, s.market, s.outcome), value_sel)

    def test_sharp_consensus_normalised(self):
        _, provider, _, _ = self.stack()
        snap = provider.fetch(["soccer"])
        event = snap.events[0]
        cons = sharp_consensus(snap.for_event(event.id))
        self.assertIn("h2h", cons)
        self.assertAlmostEqual(sum(cons["h2h"].values()), 1.0, places=6)


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(len(cfg.sports), 5)
        self.assertFalse(cfg.live_alerts_enabled())

    def test_load_missing_file_returns_defaults(self):
        cfg = load_config("no-existe-12345.toml")
        self.assertEqual(cfg.provider, "sample")


if __name__ == "__main__":
    unittest.main()
