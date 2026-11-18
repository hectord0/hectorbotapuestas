"""Interfaz de proveedores de cuotas.

Cambiar de proveedor (o añadir uno nuevo) solo requiere implementar ``fetch``.
El resto del sistema no sabe de dónde vienen las cuotas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from ..types import Event, Quote


@dataclass(frozen=True)
class Snapshot:
    events: list[Event] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)

    def for_event(self, event_id: str) -> list[Quote]:
        return [q for q in self.quotes if q.event_id == event_id]


class OddsProvider(Protocol):
    name: str

    def fetch(self, sports: Sequence[str]) -> Snapshot:
        ...
