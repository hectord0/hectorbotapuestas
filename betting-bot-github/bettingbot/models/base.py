"""Contrato que cumplen todos los modelos deportivos."""

from __future__ import annotations

from typing import Protocol, Sequence

from ..types import Event, Quote

# mercados -> selección -> probabilidad
MarketProbabilities = dict[str, dict[str, float]]


class Model(Protocol):
    """Un modelo sabe convertir un evento en probabilidades por mercado."""

    name: str

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        ...
