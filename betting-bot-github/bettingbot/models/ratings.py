"""Modelos basados en Elo y una línea base de totals.

Todos los modelos de esta familia comparten la misma interfaz y se ajustan con
listas de resultados. Los hiperparámetros (K, escala, ventaja de campo) son
razonables por defecto, pero deben calibrarse con datos reales: están
documentados para que sepas qué tocar.
"""

from __future__ import annotations

import math
from typing import Sequence

from ..types import Event, Quote, market_key
from .base import MarketProbabilities
from .elo import Elo


def _get(r, key, default=None):
    if isinstance(r, dict):
        return r.get(key, default)
    return getattr(r, key, default)


class EloModel:
    """Modelo Elo genérico: solo mercado h2h (ganador)."""

    name = "elo"

    def __init__(
        self,
        k: float = 20.0,
        scale: float = 400.0,
        home_advantage: float = 0.0,
        mov: bool = False,
        neutral: bool = False,
    ) -> None:
        self.elo = Elo(k=k, scale=scale, home_advantage=home_advantage, mov=mov)
        self.neutral = neutral
        self.fitted = False
        self.n_matches = 0

    def fit(self, results: Sequence) -> "EloModel":
        for r in results:
            score_h = float(_get(r, "home_score", _get(r, "score_home", 0)))
            score_a = float(_get(r, "away_score", _get(r, "score_away", 0)))
            self.elo.update(
                _get(r, "home"), _get(r, "away"), score_h, score_a,
                neutral=bool(_get(r, "neutral", self.neutral)),
            )
        self.n_matches = len(results)
        self.fitted = True
        return self

    def win_prob(self, home: str, away: str) -> float:
        return self.elo.win_prob(home, away, neutral=self.neutral)

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        p = self.win_prob(event.home, event.away)
        return {"h2h": {"home": p, "away": 1.0 - p}}


class TotalPointsModel:
    """Línea base para totals: media de puntos con distribución normal.

    Es deliberadamente simple. Sirve como referencia honesta y como
    "sanity check" contra las cuotas; para un modelo serio de totals hay que
    ajustar ritmo y eficiencia por equipo.
    """

    def __init__(self, sigma: float = 18.0, home_boost: float = 1.5) -> None:
        self.sigma = sigma
        self.home_boost = home_boost
        self.scored: dict[str, list[float]] = {}
        self.conceded: dict[str, list[float]] = {}

    def fit(self, results: Sequence) -> "TotalPointsModel":
        for r in results:
            h, a = _get(r, "home"), _get(r, "away")
            sh = float(_get(r, "home_score", 0))
            sa = float(_get(r, "away_score", 0))
            self.scored.setdefault(h, []).append(sh)
            self.scored.setdefault(a, []).append(sa)
            self.conceded.setdefault(h, []).append(sa)
            self.conceded.setdefault(a, []).append(sh)
        return self

    def expected_total(self, home: str, away: str) -> float:
        def avg(xs):
            return sum(xs) / len(xs) if xs else 0.0

        league_avg = (
            sum(sum(v) for v in self.scored.values())
            / max(sum(len(v) for v in self.scored.values()), 1)
        )
        home_off = avg(self.scored.get(home, [])) or league_avg
        away_off = avg(self.scored.get(away, [])) or league_avg
        home_def = avg(self.conceded.get(home, [])) or league_avg
        away_def = avg(self.conceded.get(away, [])) or league_avg
        return (home_off + away_def) / 2 + (away_off + home_def) / 2 + self.home_boost

    def over_prob(self, home: str, away: str, line: float) -> float:
        mean = self.expected_total(home, away)
        z = (line - mean) / self.sigma
        # P(total > line) con corrección de continuidad para líneas .5
        return 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


class EloWithTotals(EloModel):
    """Elo para ganador + normal para el total de puntos."""

    name = "elo+totals"

    def __init__(self, sigma: float = 18.0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.totals = TotalPointsModel(sigma=sigma)

    def fit(self, results: Sequence) -> "EloWithTotals":
        super().fit(results)
        self.totals.fit(results)
        return self

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        probs = super().probabilities(event, quotes)
        for line in (2.5, 5.5, 8.5, 200.5, 210.5, 215.5, 220.5, 225.5, 230.5):
            # Solo publicamos la línea si parece coherente con el deporte.
            if line < 100 and self._looks_like_basketball():
                continue
            if line > 100 and not self._looks_like_basketball():
                continue
            over = self.totals.over_prob(event.home, event.away, line)
            if 0.01 < over < 0.99:
                probs[market_key("totals", line)] = {
                    "over": over,
                    "under": 1.0 - over,
                }
        return probs

    def _looks_like_basketball(self) -> bool:
        scores = [s for v in self.totals.scored.values() for s in v]
        if not scores:
            return True
        return (sum(scores) / len(scores)) > 40
