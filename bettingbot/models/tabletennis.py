"""Tenis de mesa: Elo mapeado a través del modelo de puntos.

Los partidos son cortos (al mejor de 5 o 7 juegos a 11 puntos), así que la
varianza es enorme y el Elo necesita K más alto que en tenis. Además se puede
convertir la probabilidad de partido en una ventaja por punto, lo que permite
razonar sobre márgenes de juegos.
"""

from __future__ import annotations

from ..types import Event, Quote
from .base import MarketProbabilities
from .ratings import EloModel
from . import pointmodel


class TableTennisModel(EloModel):
    name = "table-tennis-elo"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("k", 32.0)
        kwargs.setdefault("scale", 400.0)
        kwargs.setdefault("home_advantage", 0.0)
        super().__init__(**kwargs)

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        p = self.win_prob(event.home, event.away)
        return {"h2h": {"home": p, "away": 1.0 - p}}

    @staticmethod
    def implied_point_edge(match_prob_a: float, best_of: int = 5) -> float:
        """Ventaja por punto (p_a - p_b) que produce esa probabilidad de partido.

        Útil para detectar cuotas incoherentes: dos jugadores con Elo similar
        no pueden tener una cuota de 1.10 en un formato al mejor de 5.
        """
        lo, hi = 0.0, 0.5
        for _ in range(80):
            mid = (lo + hi) / 2
            p = pointmodel.table_tennis_match_prob(0.5 + mid, 0.5, best_of)
            if p < match_prob_a:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
