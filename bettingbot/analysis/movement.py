"""Detección de movimientos de cuota: steam moves y drift.

La idea: una cuota que baja rápido significa dinero entrando por esa
selección. Si baja en la casa sharp (Pinnacle), suele ser información y el
resto del mercado la seguirá. Una cuota que sube (drift) suele indicar dinero
en contra o un equipo devaluado por noticias (lesiones, alineaciones).

Medimos el cambio en una *ventana* de tiempo, no desde la apertura, para no
alertar de movimientos viejos que ya están descontados en todo el mercado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from ..types import Event, Signal


@dataclass(frozen=True)
class PricePoint:
    t: datetime
    price: float


@dataclass(frozen=True)
class Movement:
    opening: float
    last: float
    low: float
    high: float
    points: int
    change_pct: float        # desde la apertura
    window_change_pct: float  # en la ventana analizada


@dataclass(frozen=True)
class MovementConfig:
    drop_pct: float = 0.08       # -8% en la ventana => steam
    rise_pct: float = 0.10       # +10% => drift
    window_min: int = 90
    min_points: int = 2
    min_price: float = 1.20
    max_price: float = 15.00


def analyse(
    points: Sequence[PricePoint], window_min: int
) -> Movement | None:
    points = sorted(points, key=lambda p: p.t)
    if len(points) < 2:
        return None
    opening = points[0].price
    last = points[-1].price
    cutoff = points[-1].t - timedelta(minutes=window_min)
    window_prices = [p.price for p in points if p.t >= cutoff]
    if not window_prices:
        window_prices = [last]
    window_start = window_prices[0]
    change_pct = (last / opening - 1.0) if opening > 0 else 0.0
    window_change = (last / window_start - 1.0) if window_start > 0 else 0.0
    return Movement(
        opening=opening,
        last=last,
        low=min(p.price for p in points),
        high=max(p.price for p in points),
        points=len(points),
        change_pct=change_pct,
        window_change_pct=window_change,
    )


def detect(
    series: Mapping[tuple, Sequence[PricePoint]],
    events: Mapping[str, Event],
    config: MovementConfig | None = None,
) -> list[Signal]:
    """``series``: ``{(event_id, market_key, outcome, bookmaker): [PricePoint]}``."""
    config = config or MovementConfig()
    raw: list[tuple[Signal, float]] = []

    for (event_id, key, outcome, bookmaker), points in series.items():
        event = events.get(event_id)
        if event is None:
            continue
        mv = analyse(points, config.window_min)
        if mv is None or mv.points < config.min_points:
            continue
        if not (config.min_price <= mv.last <= config.max_price):
            continue

        window = mv.window_change_pct
        kind = None
        if window <= -config.drop_pct:
            kind = "STEAM"
        elif window >= config.rise_pct:
            kind = "DRIFT"
        if kind is None:
            continue

        severity = 3 if abs(window) >= 2 * config.drop_pct else 2
        arrows = "↓" if kind == "STEAM" else "↑"
        raw.append(
            (
                Signal(
                    kind=kind,
                    sport=event.sport,
                    league=event.league,
                    match=event.label,
                    commence=event.commence,
                    market=key,
                    outcome=outcome,
                    bookmaker=bookmaker,
                    price=mv.last,
                    detail=(
                        f"{mv.opening:.2f} → {mv.last:.2f} {arrows} "
                        f"({window:+.1%} en {config.window_min} min, "
                        f"rango {mv.low:.2f}-{mv.high:.2f})"
                    ),
                    severity=severity,
                    event_id=event_id,
                ),
                window,
            )
        )

    return _aggregate(raw)


SHARP_BOOKS = ("pinnacle", "betfair", "smarkets", "matchbook")


def _aggregate(raw: list[tuple[Signal, float]]) -> list[Signal]:
    """Un movimiento de mercado suele mover varias casas a la vez.

    Agrupamos por (evento, mercado, selección, tipo) y dejamos una sola alerta,
    prefiriendo la casa sharp como referencia. Si no, la de mayor movimiento.
    """
    groups: dict[tuple, list[tuple[Signal, float]]] = {}
    for signal, change in raw:
        groups.setdefault(
            (signal.event_id, signal.market, signal.outcome, signal.kind), []
        ).append((signal, change))

    out: list[Signal] = []
    for group in groups.values():
        sharp = [
            (s, c) for s, c in group if s.bookmaker.lower() in SHARP_BOOKS
        ]
        pool = sharp or group
        chosen, change = max(pool, key=lambda sc: abs(sc[1]))
        if len(group) > 1:
            detail = f"{len(group)} casas movidas · {chosen.detail}"
            chosen = Signal(
                kind=chosen.kind,
                sport=chosen.sport,
                league=chosen.league,
                match=chosen.match,
                commence=chosen.commence,
                market=chosen.market,
                outcome=chosen.outcome,
                bookmaker=chosen.bookmaker,
                price=chosen.price,
                detail=detail,
                severity=chosen.severity,
                event_id=chosen.event_id,
            )
        out.append(chosen)
    out.sort(key=lambda s: s.severity, reverse=True)
    return out
