"""Modelo de córners con binomial negativa.

Los córners están **sobredispersos**: la media ronda 10, pero hay partidos de 3
y partidos de 20. Una Poisson con media 10 asignaría muy poca probabilidad a
las colas. La binomial negativa (con ``size`` ajustable) modela esa varianza
extra y evita cuotas "value" falsas por colas mal estimadas.

Efectos incluidos:

* Media de la liga (catálogo) y factor por equipo si hay datos.
* ``event.extra["pace"]``: equipos que atacan por bandas generan más córners.
* ``event.extra["must_win"]``: un equipo que necesita ganar suele volcar el
  juego por fuera y subir el total.
"""

from __future__ import annotations

from typing import Sequence

from .. import leagues
from ..types import Event, Quote, market_key, split_key
from .base import MarketProbabilities
from .counts import over_prob_negbinom

DEFAULT_CORNERS = 10.0
DEFAULT_SIZE = 9.0  # dispersión: menor = colas más gordas
DEFAULT_LINES = (8.5, 9.5, 10.5, 11.5)


def _get(r, key, default=None):
    if isinstance(r, dict):
        return r.get(key, default)
    return getattr(r, key, default)


class CornersModel:
    name = "corners-negbinomial"

    def __init__(
        self,
        default_corners: float = DEFAULT_CORNERS,
        size: float = DEFAULT_SIZE,
        must_win_boost: float = 1.10,
    ):
        self.default_corners = default_corners
        self.size = size
        self.must_win_boost = must_win_boost
        self.team_corners: dict[str, float] = {}
        self.league_mean: float = default_corners
        self.fitted = False

    def fit(self, results: Sequence) -> "CornersModel":
        totals: list[float] = []
        per_team: dict[str, list[float]] = {}
        for r in results:
            hc = _get(r, "home_corners")
            ac = _get(r, "away_corners")
            if hc is None or ac is None:
                continue
            hc, ac = float(hc), float(ac)
            totals.append(hc + ac)
            per_team.setdefault(_get(r, "home"), []).append(hc)
            per_team.setdefault(_get(r, "away"), []).append(ac)
        if not totals:
            return self
        self.league_mean = sum(totals) / len(totals)
        half = self.league_mean / 2.0 or 1.0
        self.team_corners = {
            team: (sum(vals) / len(vals)) / half for team, vals in per_team.items()
        }
        self.fitted = True
        return self

    def expected_corners(self, event: Event) -> float:
        profile = leagues.find(event.league)
        base = (
            profile.corners
            if profile is not None and profile.sport == "soccer" and profile.corners
            else self.league_mean or self.default_corners
        )
        f_home = self.team_corners.get(event.home, 1.0) if self.fitted else 1.0
        f_away = self.team_corners.get(event.away, 1.0) if self.fitted else 1.0
        lam = base * (f_home + f_away) / 2.0

        extra = event.extra or {}
        pace = extra.get("pace")
        if pace is not None:
            lam *= float(pace)
        if extra.get("must_win"):
            lam *= self.must_win_boost
        return max(lam, 1.0)

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        lam = self.expected_corners(event)
        lines = sorted(
            {
                point
                for q in (quotes or [])
                for market, point in [split_key(q.key)]
                if market == "corners" and point is not None
            }
            or set(DEFAULT_LINES)
        )
        out: MarketProbabilities = {}
        for line in lines:
            over = over_prob_negbinom(lam, self.size, line)
            out[market_key("corners", line)] = {"over": over, "under": 1.0 - over}
        return out
