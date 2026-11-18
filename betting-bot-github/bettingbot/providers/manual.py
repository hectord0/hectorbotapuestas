"""Proveedor manual: cuotas que escribes tú en un JSON.

The Odds API no ofrece de forma fiable mercados de **tarjetas y córners**, y en
ligas menores la cobertura es irregular. Este proveedor lee un archivo JSON con
los eventos y las cuotas que hayas visto en las casas, de modo que el mismo
motor de análisis (devig, consenso, value, errores, movimientos) se aplica a
esos mercados.

Formato (``data/odds_manual.json``)::

    {
      "events": [
        {
          "id": "manual-palermo-bari",
          "sport": "soccer",
          "league": "Serie B",
          "home": "Palermo",
          "away": "Bari",
          "commence": "2026-11-18T20:00:00+00:00",
          "extra": {"derby": true, "referee_factor": 1.2},
          "quotes": [
            {"bookmaker": "bet365", "market": "cards", "outcome": "over",
             "point": 4.5, "price": 2.05},
            {"bookmaker": "pinnacle", "market": "corners", "outcome": "under",
             "point": 10.5, "price": 1.95}
          ]
        }
      ]
    }

El archivo se relee en cada ``fetch``: puedes editarlo con el bot vigilando y
los cambios entran en el siguiente sondeo.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

from ..types import Event, Quote, utcnow
from .base import Snapshot

DEFAULT_PATH = "data/odds_manual.json"


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return utcnow()
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return utcnow()
    return dt if dt.tzinfo else dt.replace(tzinfo=utcnow().tzinfo)


class ManualProvider:
    name = "manual"

    def __init__(self, path: str | Path = DEFAULT_PATH, verbose: bool = True) -> None:
        self.path = Path(path)
        self.verbose = verbose

    def _load(self) -> dict:
        if not self.path.exists():
            if self.verbose:
                print(
                    f"[manual] no existe {self.path}; crea el archivo o cambia "
                    f"provider en config.toml",
                    file=sys.stderr,
                )
            return {"events": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[manual] error leyendo {self.path}: {exc}", file=sys.stderr)
            return {"events": []}

    def fetch(self, sports: Sequence[str]) -> Snapshot:
        data = self._load()
        events: list[Event] = []
        quotes: list[Quote] = []

        for raw in data.get("events", []) or []:
            sport = raw.get("sport", "soccer")
            if sports and sport not in sports:
                continue
            home, away = raw.get("home"), raw.get("away")
            if not home or not away:
                continue
            event = Event(
                id=str(raw.get("id") or f"manual-{home}-{away}"),
                sport=sport,
                league=raw.get("league", ""),
                home=home,
                away=away,
                commence=_parse_dt(raw.get("commence")),
                extra=raw.get("extra") or {},
            )
            events.append(event)

            for row in raw.get("quotes", raw.get("odds", [])) or []:
                book = row.get("bookmaker") or row.get("book")
                market = row.get("market")
                outcome = row.get("outcome")
                price = row.get("price") or row.get("odds")
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue
                if not book or not market or not outcome or price <= 1.0:
                    continue
                point = row.get("point")
                quotes.append(
                    Quote(
                        event_id=event.id,
                        bookmaker=str(book),
                        market=str(market),
                        outcome=str(outcome),
                        price=price,
                        point=float(point) if point is not None else None,
                        captured_at=_parse_dt(row.get("captured_at")) if row.get("captured_at") else utcnow(),
                    )
                )

        return Snapshot(events=events, quotes=quotes)
