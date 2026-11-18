"""Elo genérico, con multiplicador por margen de victoria.

Se usa para baloncesto, tenis, MMA y tenis de mesa. Elo es simple, robusto y
sorprendentemente difícil de batir: es la base de muchos sistemas reales.

La probabilidad de que A gane se deriva de la diferencia de rating::

    P(A) = 1 / (1 + 10 ** (-(r_a - r_b) / scale))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def expected_score(rating_a: float, rating_b: float, scale: float = 400.0) -> float:
    return 1.0 / (1.0 + 10.0 ** (-(rating_a - rating_b) / scale))


class Elo:
    """Sistema Elo con K configurable y ventaja de campo en puntos de rating."""

    def __init__(
        self,
        k: float = 20.0,
        scale: float = 400.0,
        home_advantage: float = 0.0,
        initial: float = 1500.0,
        mov: bool = False,
    ) -> None:
        self.k = k
        self.scale = scale
        self.home_advantage = home_advantage
        self.initial = initial
        self.mov = mov  # multiplicador por margen de victoria
        self.ratings: dict[str, float] = {}
        self.played: dict[str, int] = {}

    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial)

    def win_prob(self, a: str, b: str, neutral: bool = False) -> float:
        diff = self.rating(a) - self.rating(b)
        if not neutral:
            diff += self.home_advantage
        return expected_score(diff, 0.0, self.scale)

    def update(
        self, a: str, b: str, score_a: float, score_b: float, neutral: bool = False
    ) -> None:
        """``score_a``/``score_b`` son 1/0 (o 0.5 en empate)."""
        diff = self.rating(a) - self.rating(b)
        if not neutral:
            diff += self.home_advantage
        expected_a = expected_score(diff, 0.0, self.scale)
        actual_a = 1.0 if score_a > score_b else (0.0 if score_a < score_b else 0.5)
        mult = 1.0
        if self.mov:
            margin = abs(score_a - score_b)
            # 538-style: el margen importa, pero con rendimientos decrecientes.
            mult = math.log1p(margin) * (2.2 / ((self.rating(a) - self.rating(b)) * 0.001 + 2.2))
        delta = self.k * mult * (actual_a - expected_a)
        self.ratings[a] = self.rating(a) + delta
        self.ratings[b] = self.rating(b) - delta
        self.played[a] = self.played.get(a, 0) + 1
        self.played[b] = self.played.get(b, 0) + 1

    def fit(
        self,
        results,
        home_key: str = "home",
        away_key: str = "away",
        score_home_key: str = "home_score",
        score_away_key: str = "away_score",
    ) -> "Elo":
        """Ajusta desde una lista de dicts o dataclasses con esos campos."""
        for r in results:
            get = (lambda k: r[k]) if isinstance(r, dict) else (lambda k: getattr(r, k))
            self.update(
                get(home_key),
                get(away_key),
                float(get(score_home_key)),
                float(get(score_away_key)),
            )
        return self

    def top(self, n: int = 10):
        return sorted(self.ratings.items(), key=lambda kv: kv[1], reverse=True)[:n]
