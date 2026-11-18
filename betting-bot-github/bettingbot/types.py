"""Tipos de datos compartidos por proveedores, modelos, análisis y alertas.

Todo el sistema trabaja con estructuras simples e inmutables para que sea
trivial serializarlas a SQLite o a JSON sin dependencias externas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Ahora en UTC, siempre con tzinfo. Nunca usamos horas locales."""
    return datetime.now(timezone.utc)


def market_key(market: str, point: Optional[float] = None) -> str:
    """Clave canónica de un mercado, incluyendo la línea cuando existe.

    Ejemplos: ``h2h``, ``totals@2.5``, ``spreads@-4.5``.
    """
    if point is None:
        return market
    return f"{market}@{point:g}"


def split_key(key: str) -> tuple[str, Optional[float]]:
    """Inversa de :func:`market_key`."""
    if "@" not in key:
        return key, None
    market, _, raw = key.partition("@")
    try:
        return market, float(raw)
    except ValueError:
        return key, None


@dataclass(frozen=True)
class Event:
    """Un partido / combate / encuentro."""

    id: str
    sport: str
    league: str
    home: str
    away: str
    commence: datetime
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.home} vs {self.away}"

    @property
    def is_pending(self) -> bool:
        return self.commence > utcnow()


@dataclass(frozen=True)
class Quote:
    """Una cuota concreta de una casa para una selección de un mercado."""

    event_id: str
    bookmaker: str
    market: str            # "h2h" | "totals" | "spreads" | ...
    outcome: str           # "home" | "draw" | "away" | "over" | "under"
    price: float
    point: Optional[float] = None
    captured_at: datetime = field(default_factory=utcnow)

    @property
    def key(self) -> str:
        return market_key(self.market, self.point)

    @property
    def selection(self) -> tuple[str, str]:
        return (self.key, self.outcome)


@dataclass(frozen=True)
class Signal:
    """Algo que merece una alerta: value, arbitraje, error de casa, movimiento."""

    kind: str                  # VALUE | ARB | MISPRICING | STEAM | DRIFT | DIVERGENCE
    sport: str
    league: str
    match: str
    commence: Optional[datetime]
    market: str
    outcome: str
    bookmaker: str
    price: float
    fair_prob: Optional[float] = None
    edge: Optional[float] = None      # ventaja esperada en % (0.05 = 5%)
    stake: Optional[float] = None     # importe sugerido en unidades monetarias
    detail: str = ""
    severity: int = 1                 # 1 informativo, 2 importante, 3 urgente
    event_id: str = ""
    factors: str = ""                 # explicación breve del partido/jugadores
    single_book: bool = False         # apuesta recomendada en una sola casa

    @property
    def dedupe_key(self) -> str:
        """Identidad estable para no repetir la misma alerta cada minuto.

        Los movimientos se agrupan por mercado (no por casa) y el arbitraje
        por mercado, porque el detalle cambia en cada sondeo y si no se
        duplicarían sin parar.
        """
        if self.kind in ("STEAM", "DRIFT"):
            return f"{self.kind}|{self.event_id}|{self.market}|{self.outcome}"
        if self.kind == "ARB":
            return f"ARB|{self.event_id}|{self.market}"
        return f"{self.kind}|{self.event_id}|{self.market}|{self.outcome}|{self.bookmaker}"

    def title(self) -> str:
        icons = {
            "VALUE": "💰",
            "ARB": "🔒",
            "MISPRICING": "⚠️",
            "STEAM": "🚨",
            "DRIFT": "📈",
            "DIVERGENCE": "↔️",
        }
        names = {
            "VALUE": "Value bet",
            "ARB": "Arbitraje",
            "MISPRICING": "Posible error de casa",
            "STEAM": "Steam move (baja)",
            "DRIFT": "Drift (sube)",
            "DIVERGENCE": "Divergencia entre casas",
        }
        return f"{icons.get(self.kind, '•')} {names.get(self.kind, self.kind)}"


@dataclass
class MarketQuotes:
    """Agrupa las cuotas de un mercado concreto para facilitar el análisis."""

    key: str
    market: str
    point: Optional[float]
    # bookmaker -> outcome -> price
    prices: dict[str, dict[str, float]] = field(default_factory=dict)
    # bookmaker -> outcome -> last_update
    updated: dict[str, dict[str, datetime]] = field(default_factory=dict)

    def outcomes(self) -> list[str]:
        found: list[str] = []
        for book in self.prices.values():
            for outcome in book:
                if outcome not in found:
                    found.append(outcome)
        return found

    def complete_books(self) -> dict[str, dict[str, float]]:
        """Solo casas que tienen todas las selecciones del mercado."""
        wanted = sorted(self.outcomes())
        return {
            book: dict(row)
            for book, row in self.prices.items()
            if sorted(row) == wanted and all(p > 1.0 for p in row.values())
        }
