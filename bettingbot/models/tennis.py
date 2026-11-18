"""Tenis: Elo por superficie + modelo de puntos (servicio/resto).

El Elo general es la señal principal. El Elo por superficie se mezcla con el
general según cuántos partidos haya en esa superficie: con poca muestra pesa
poco, con mucha manda la superficie.

Además se expone el modelo de puntos de ``pointmodel``: si tienes estadísticas
de servicio (porcentaje de puntos ganados al saque), se convierte directamente
en probabilidad de partido, que suele batir al Elo puro.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Sequence

from ..types import Event, Quote
from .base import MarketProbabilities
from .elo import Elo
from . import pointmodel


def _get(r, key, default=None):
    if isinstance(r, dict):
        return r.get(key, default)
    return getattr(r, key, default)


def _match_prob_from_sets(s: float, best_of: int) -> float:
    """Probabilidad de ganar el partido sabiendo la probabilidad por set."""
    if best_of == 3:
        return s * s * (3.0 - 2.0 * s)
    needed = best_of // 2 + 1
    total = 0.0
    for won_sets in range(needed, best_of + 1):
        lost = best_of - won_sets
        # El último set siempre lo gana quien gana el partido.
        from math import comb

        total += comb(lost + needed - 2, needed - 1) * s**needed * (1 - s) ** lost
    return total


def set_win_prob(match_prob: float, best_of: int = 3) -> float:
    """Invierte el modelo de sets para obtener la probabilidad por set."""
    lo, hi = 0.001, 0.999
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _match_prob_from_sets(mid, best_of) < match_prob:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def at_least_one_set_prob(match_prob: float, best_of: int = 3) -> float:
    """P(ganar al menos un set) para quien tiene ``match_prob`` de ganar."""
    s = set_win_prob(match_prob, best_of)
    needed = best_of // 2 + 1
    return 1.0 - (1.0 - s) ** needed


class TennisModel:
    name = "tennis-surface-elo"

    def __init__(self, k: float = 24.0, scale: float = 400.0, surface_weight: float = 1.5):
        self.overall = Elo(k=k, scale=scale)
        self.by_surface: dict[str, Elo] = defaultdict(lambda: Elo(k=k, scale=scale))
        self.surface_matches: dict[str, int] = defaultdict(int)
        self.min_surface_matches = 5
        self.surface_weight = surface_weight
        self.fitted = False

    def fit(self, results: Sequence) -> "TennisModel":
        for r in results:
            home, away = _get(r, "home"), _get(r, "away")
            sh = float(_get(r, "home_score", 0))
            sa = float(_get(r, "away_score", 0))
            self.overall.update(home, away, sh, sa)
            surface = _get(r, "surface", None)
            if surface:
                self.by_surface[surface].update(home, away, sh, sa)
                self.surface_matches[surface] += 1
        self.fitted = True
        return self

    def _surface_elo(self, surface: str | None) -> Elo | None:
        if not surface:
            return None
        if self.surface_matches.get(surface, 0) < self.min_surface_matches:
            return None
        return self.by_surface.get(surface)

    def win_prob(self, player_a: str, player_b: str, surface: str | None = None) -> float:
        base = self.overall.win_prob(player_a, player_b)
        surf = self._surface_elo(surface)
        if surf is None:
            return base
        surf_p = surf.win_prob(player_a, player_b)
        # Media en log-odds para mezclar probabilidades de forma estable.
        def logit(p):
            p = min(max(p, 1e-6), 1 - 1e-6)
            return p / (1 - p)

        w = self.surface_weight
        mixed = logit(surf_p) * w + logit(base) * 1.0
        return 1.0 / (1.0 + math.exp(-mixed))

    @staticmethod
    def point_model_prob(
        serve_points_a: float, serve_points_b: float, best_of: int = 3
    ) -> float:
        return pointmodel.match_win_prob(serve_points_a, serve_points_b, best_of)

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        surface = event.extra.get("surface") if event.extra else None
        p = self.win_prob(event.home, event.away, surface)
        best_of = int((event.extra or {}).get("best_of", 3) or 3)
        out = {"h2h": {"home": p, "away": 1.0 - p}}
        home_set = at_least_one_set_prob(p, best_of)
        away_set = at_least_one_set_prob(1.0 - p, best_of)
        out["sets_home@0.5"] = {"over": home_set, "under": 1.0 - home_set}
        out["sets_away@0.5"] = {"over": away_set, "under": 1.0 - away_set}
        return out
