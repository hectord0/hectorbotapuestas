"""Registro de modelos por deporte.

Un deporte puede tener **varios modelos** que aportan mercados distintos: el de
fútbol produce 1X2, goles y hándicap; el de tarjetas produce ``cards@X``; el de
córners produce ``corners@X``. El pipeline los combina quedándose con el primer
modelo que define cada mercado.
"""

from __future__ import annotations

from typing import Iterable

from .base import MarketProbabilities, Model
from .basketball import BasketballModel
from .cards import CardsModel
from .corners import CornersModel
from .football import FootballModel, HalfTimeModel, MatchResult
from .mma import MMAModel
from .tabletennis import TableTennisModel
from .tennis import TennisModel

SPORTS = ("soccer", "basketball", "tennis", "mma", "table_tennis")

MODEL_CLASSES = {
    "soccer": FootballModel,
    "basketball": BasketballModel,
    "tennis": TennisModel,
    "mma": MMAModel,
    "table_tennis": TableTennisModel,
}


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, list[Model]] = {}

    def register(self, sport: str, model: Model) -> None:
        bucket = self._models.setdefault(sport, [])
        if model not in bucket:
            bucket.append(model)

    def get(self, sport: str) -> Model | None:
        bucket = self._models.get(sport)
        return bucket[0] if bucket else None

    def get_all(self, sport: str) -> list[Model]:
        return list(self._models.get(sport, []))

    def has(self, sport: str) -> bool:
        return bool(self._models.get(sport))

    def sports(self) -> list[str]:
        return sorted(s for s, models in self._models.items() if models)

    def combined(
        self, sport: str, event, quotes
    ) -> MarketProbabilities:
        """Une los mercados de todos los modelos del deporte (el primero manda)."""
        out: MarketProbabilities = {}
        for model in self.get_all(sport):
            try:
                produced = model.probabilities(event, quotes) or {}
            except Exception as exc:  # un modelo roto no tumba el resto
                print(f"[modelo] {getattr(model, 'name', model)}: {exc}")
                continue
            for market, outcomes in produced.items():
                out.setdefault(market, outcomes)
        return out

    def describe(self) -> str:
        if not self._models:
            return "sin modelos ajustados (solo se alertará de movimientos y errores)"
        lines = []
        for sport, models in sorted(self._models.items()):
            names = ", ".join(getattr(m, "name", type(m).__name__) for m in models)
            lines.append(f"  - {sport}: {names}")
        return "\n".join(lines)


__all__ = [
    "ModelRegistry",
    "MODEL_CLASSES",
    "SPORTS",
    "MarketProbabilities",
    "Model",
    "FootballModel",
    "HalfTimeModel",
    "MatchResult",
    "CardsModel",
    "CornersModel",
]
