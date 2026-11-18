"""MMA y boxeo.

El Elo puro es una base decente y difícil de batir en deportes de combate,
donde la muestra por peleador es pequeña. K alto (28) porque cada pelea es muy
informativa.

Cuando ``event.extra`` trae datos de los peleadores se usan ajustes simples:
edad, inactividad y diferencia de alcance. Todos son opcionales: sin datos,
el modelo funciona igual con Elo.
"""

from __future__ import annotations

import math
from typing import Sequence

from ..types import Event, Quote
from .base import MarketProbabilities
from .ratings import EloModel


class MMAModel(EloModel):
    name = "mma-elo"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("k", 28.0)
        kwargs.setdefault("scale", 400.0)
        kwargs.setdefault("home_advantage", 0.0)
        super().__init__(**kwargs)

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        p = self.win_prob(event.home, event.away)
        extra = event.extra or {}
        p = self._adjust(p, extra.get("fighter_a"), extra.get("fighter_b"))
        p = min(max(p, 0.02), 0.98)
        return {"h2h": {"home": p, "away": 1.0 - p}}

    @staticmethod
    def _adjust(p: float, a: dict | None, b: dict | None) -> float:
        """Ajustes en log-odds por edad, inactividad y alcance."""
        if not a or not b:
            return p

        def logit(x: float) -> float:
            x = min(max(x, 1e-6), 1 - 1e-6)
            return math.log(x / (1 - x))

        z = logit(p)
        for fighter, sign in ((a, 1.0), (b, -1.0)):
            age = fighter.get("age")
            if age is not None and age > 33:
                z -= sign * 0.05 * (age - 33)
            layoff = fighter.get("months_inactive")
            if layoff is not None and layoff > 12:
                z -= sign * 0.02 * (layoff - 12)
            reach = fighter.get("reach_cm")
            other = (b if fighter is a else a).get("reach_cm")
            if reach is not None and other is not None:
                z += sign * 0.015 * (reach - other)
        return 1.0 / (1.0 + math.exp(-z))
