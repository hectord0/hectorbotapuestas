"""Pipeline: sondeo → almacenamiento → análisis → alertas."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import timedelta

from . import devig, factors, leagues
from .alerts import Notifier, format_signal
from .analysis import errors, movement, value
from .config import Config, error_config, movement_config, value_config
from .models.registry import ModelRegistry
from .providers.base import OddsProvider
from .safety import assess
from .storage import Storage
from .timeutil import in_day, in_days
from .types import Signal, utcnow


@dataclass
class RunResult:
    events: int = 0
    quotes: int = 0
    signals: list[Signal] = field(default_factory=list)
    sent: int = 0
    failures: int = 0
    snapshot: object | None = None

    def count_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.signals:
            out[s.kind] = out.get(s.kind, 0) + 1
        return out


def sharp_consensus(
    event_quotes, method: str = "shin"
) -> dict[str, dict[str, float]]:
    """Consenso de mercado por mercado, ponderando casas sharps."""
    grouped: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for q in event_quotes:
        grouped[q.key].setdefault(q.bookmaker, {})[q.outcome] = q.price
    out: dict[str, dict[str, float]] = {}
    for key, prices in grouped.items():
        probs = devig.book_probabilities(prices, method)
        cons = devig.consensus(probs)
        if cons:
            out[key] = cons
    return out


def day_mode_for(cfg) -> str:
    return getattr(cfg, "event_day", "days:1") or "days:1"


def single_book_convert(signals, cfg) -> list:
    """Elimina arbitrajes y convierte errores de casa en apuestas simples.

    El usuario apuesta solo en una casa: no se le ofrecen combinaciones de
    varias casas. Un MISPRICING con probabilidad y ventaja se convierte en una
    apuesta VALUE normal en esa casa concreta, con Kelly calculado.
    """
    if not getattr(cfg, "single_book_only", True):
        return list(signals)
    cfg_value = value_config(cfg)
    out: list[Signal] = []
    for s in signals:
        if s.kind == "ARB":
            continue
        if (
            s.kind == "MISPRICING"
            and s.fair_prob
            and s.edge
            and s.edge > 0
            and cfg_value.min_price <= s.price <= cfg_value.max_price
        ):
            stake = value.kelly_stake(s.fair_prob, s.price, cfg_value)
            if stake > 0:
                out.append(
                    replace(
                        s,
                        kind="VALUE",
                        stake=stake,
                        severity=2,
                        single_book=True,
                        detail=(
                            f"consenso {s.fair_prob:.1%} vs cuota {s.price:.2f} "
                            f"(justa {1 / s.fair_prob:.2f}) · "
                            f"apuesta simple en {s.bookmaker}"
                        ),
                    )
                )
                continue
        out.append(s)
    return out


def analyse(
    events: dict,
    quotes,
    registry: ModelRegistry,
    cfg: Config,
    series: dict | None = None,
) -> list[Signal]:
    by_event: dict[str, list] = defaultdict(list)
    for q in quotes:
        by_event[q.event_id].append(q)

    allowed_leagues = set(leagues.filter_keys(cfg.leagues)) if cfg.leagues else None
    day_mode = day_mode_for(cfg)
    error_days = getattr(cfg, "error_scan_days", 7)
    ERROR_KINDS = {"ARB", "MISPRICING", "DIVERGENCE"}

    signals: list[Signal] = []
    for event in events.values():
        match_window = in_day(event.commence, day_mode)
        error_window = in_days(event.commence, error_days)
        if not (match_window or error_window):
            continue
        eq = by_event.get(event.id, [])
        if not eq:
            continue

        profile = leagues.find(event.league)
        if allowed_leagues and profile is not None and profile.key not in allowed_leagues:
            continue

        model_probs = registry.combined(event.sport, event, eq)
        cons = sharp_consensus(eq, cfg.errors.method)
        event_signals: list[Signal] = []
        if match_window:
            event_signals.extend(
                value.find_value_bets(event, eq, model_probs, value_config(cfg), cons)
            )
        if error_window:
            event_signals.extend(errors.detect_errors(event, eq, error_config(cfg)))

        # Errores graves (severidad 3) se marcan explícitamente para que el
        # usuario sepa que merecen revisión, aunque el partido esté lejos.
        event_signals = [
            replace(
                sig,
                detail=(
                    f"{sig.detail} · ERROR GRAVE (revisar)" if sig.detail
                    else "ERROR GRAVE (revisar)"
                ),
            )
            if sig.severity >= 3 and sig.kind in ERROR_KINDS
            else sig
            for sig in event_signals
        ]

        # Factores del partido/jugadores, para explicar la señal en la alerta.
        factor_text = factors.event_summary(event, profile)
        event_signals = [replace(s, factors=factor_text) for s in event_signals]

        # Ligas blandas: más probabilidad de precio erróneo. Subimos la
        # severidad para que el orden de las alertas refleje la oportunidad.
        if cfg.prioritize_error_prone and profile is not None and profile.priority >= 4:
            note = f"{profile.name} (riesgo de error {profile.error_risk})"
            event_signals = [
                replace(
                    sig,
                    severity=min(sig.severity + 1, 3),
                    detail=(
                        f"{sig.detail} · {note}" if sig.detail else note
                    ),
                )
                for sig in event_signals
            ]
        signals.extend(event_signals)

    if series:
        signals.extend(movement.detect(series, events, movement_config(cfg)))

    # Un VALUE ya explica el edge; no duplicamos con MISPRICING/DIVERGENCE.
    value_selections = {
        (s.event_id, s.market, s.outcome) for s in signals if s.kind == "VALUE"
    }
    def in_window(sig: Signal) -> bool:
        return in_days(sig.commence, error_days) if sig.kind in ERROR_KINDS else in_day(sig.commence, day_mode)

    filtered = [
        s
        for s in signals
        if in_window(s)
        and not (
            s.kind in ("MISPRICING", "DIVERGENCE")
            and (s.event_id, s.market, s.outcome) in value_selections
        )
    ]

    filtered.sort(key=lambda s: (s.severity, s.edge or 0.0), reverse=True)
    filtered = single_book_convert(filtered, cfg)

    safety_mode = str(getattr(cfg, "safety_mode", "safe")).lower()
    if safety_mode == "safe":
        single = getattr(cfg, "single_book_only", True)
        max_rank = {"BAJO": 1, "MEDIO": 2, "ALTO": 3}.get(
            str(getattr(cfg, "max_risk_level", "MEDIO")).upper(), 2
        )

        def threshold(s):
            if single and s.kind == "VALUE":
                return getattr(cfg, "min_single_book_score", 45)
            return getattr(cfg, "min_safe_score", 70)

        filtered = [
            s for s in filtered
            if assess(s).score >= threshold(s)
            and {"BAJO": 1, "MEDIO": 2, "ALTO": 3}.get(assess(s).level, 3) <= max_rank
        ]
        filtered.sort(key=lambda s: assess(s).score, reverse=True)
    return filtered


def run_once(
    provider: OddsProvider,
    storage: Storage,
    registry: ModelRegistry,
    notifier: Notifier,
    cfg: Config,
    history_days: int = 7,
) -> RunResult:
    snapshot = provider.fetch(cfg.sports)
    storage.upsert_events(snapshot.events)
    storage.add_quotes(snapshot.quotes)

    events = storage.get_events()
    series = storage.price_history(since=utcnow() - timedelta(days=history_days))
    signals = analyse(events, snapshot.quotes, registry, cfg, series)

    result = RunResult(
        events=len(snapshot.events),
        quotes=len(snapshot.quotes),
        signals=signals,
        snapshot=snapshot,
    )
    notify_mode = getattr(cfg, "notify_day", "") or day_mode_for(cfg)
    focus = getattr(cfg, "focus_actionable", True)
    for signal in signals:
        risk = assess(signal)
        # Prioridad: solo molestar con apuestas que de verdad se recomiendan.
        if focus and risk.decision != "APOSTAR":
            continue
        # Riesgo medio: se permite, pero con la mitad de stake.
        if risk.decision == "APOSTAR" and risk.level == "MEDIO" and signal.stake:
            signal = replace(
                signal,
                stake=round(signal.stake * 0.5, 2),
                detail=f"{signal.detail} · riesgo medio: stake reducido a la mitad",
            )
        # Los errores se pueden buscar hasta 7 días, pero solo se notifican
        # los partidos dentro del rango elegido (1-3 días por defecto).
        if not in_day(signal.commence, notify_mode):
            continue
        if not storage.should_alert(signal.dedupe_key, cfg.alert_cooldown_min):
            continue
        text = notifier.format(signal) if hasattr(notifier, "format") else format_signal(signal)
        ok = notifier.send(text)
        storage.log_alert(signal, notifier.name, ok)
        if ok:
            result.sent += 1
        else:
            result.failures += 1

    return result
