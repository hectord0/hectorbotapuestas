"""The Odds API (the-odds-api.com) como proveedor de cuotas.

Diseño defensivo:
* Descubre los deportes activos con ``/v4/sports`` y filtra por prefijo, en
  lugar de hardcodear claves que cambian con el tiempo.
* Limita cuántas ligas pide por deporte para no quemar la cuota del plan.
* Tolera que un deporte no exista (por ejemplo, tenis de mesa no suele estar).

Nota de cuota: cada llamada a ``/odds`` consume créditos. Con 500/mes gratis,
conviene sondear cada 5-10 minutos y pocas ligas.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Sequence

from ..types import Event, Quote, utcnow
from .base import Snapshot

BASE = "https://api.the-odds-api.com/v4"

SPORT_PREFIXES = {
    "soccer": ("soccer_",),
    "basketball": ("basketball_",),
    "tennis": ("tennis_",),
    "mma": ("mma_", "boxing_"),
    "table_tennis": ("table_tennis_", "ping_pong_"),
}

OUTCOME_MAP_DRAW = {"draw", "tie", "empate"}


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return utcnow()


class OddsApiProvider:
    name = "oddsapi"

    def __init__(
        self,
        api_key: str,
        regions: str = "eu",
        markets: str = "h2h,spreads,totals",
        max_leagues_per_sport: int = 3,
        timeout: float = 20.0,
        verbose: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("falta ODDS_API_KEY para usar The Odds API")
        self.api_key = api_key
        self.regions = regions
        self.markets = markets
        self.max_leagues_per_sport = max_leagues_per_sport
        self.timeout = timeout
        self.verbose = verbose
        self.remaining: str | None = None
        self._sport_keys: dict[str, list[str]] | None = None

    def _get(self, path: str, params: dict) -> tuple[object, dict]:
        params = {**params, "apiKey": self.api_key}
        url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self.remaining = resp.headers.get("x-requests-remaining", self.remaining)
                return json.loads(resp.read().decode("utf-8")), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"The Odds API HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"sin conexión con The Odds API: {exc.reason}") from exc

    def discover(self) -> dict[str, list[str]]:
        data, _ = self._get("sports", {})
        mapping: dict[str, list[str]] = {sport: [] for sport in SPORT_PREFIXES}
        if not isinstance(data, list):
            return mapping
        for item in data:
            key = item.get("key", "")
            if item.get("active") is False:
                continue
            for sport, prefixes in SPORT_PREFIXES.items():
                if any(key.startswith(p) for p in prefixes):
                    mapping[sport].append(key)
        self._sport_keys = mapping
        return mapping

    @staticmethod
    def _as_outcome(name: str, market: str, home: str, away: str) -> str | None:
        lowered = name.strip().lower()
        if market in ("h2h", "h2h_ht"):
            if lowered in OUTCOME_MAP_DRAW:
                return "draw"
            if lowered == home.strip().lower():
                return "home"
            if lowered == away.strip().lower():
                return "away"
            return None
        if market in ("sets_home", "sets_away"):
            if lowered in ("over", "sí", "si", "yes", "gana al menos un set"):
                return "over"
            if lowered in ("under", "no", "pierde todos los sets"):
                return "under"
            return None
        if market == "totals":
            if lowered in ("over", "más", "mas"):
                return "over"
            if lowered in ("under", "menos"):
                return "under"
            return None
        if market == "spreads":
            if lowered == home.strip().lower():
                return "home"
            if lowered == away.strip().lower():
                return "away"
            return None
        if market == "btts":
            if lowered in ("yes", "sí", "si"):
                return "yes"
            if lowered in ("no",):
                return "no"
            return None
        return lowered

    def fetch(self, sports: Sequence[str]) -> Snapshot:
        keys_by_sport = self._sport_keys or self.discover()
        events: dict[str, Event] = {}
        quotes: list[Quote] = []

        for sport in sports:
            keys = keys_by_sport.get(sport, [])[: self.max_leagues_per_sport]
            if not keys:
                if self.verbose:
                    print(f"[oddsapi] sin ligas disponibles para {sport}", file=sys.stderr)
                continue
            for key in keys:
                try:
                    data, _ = self._get(
                        f"sports/{key}/odds",
                        {
                            "regions": self.regions,
                            "markets": self.markets,
                            "oddsFormat": "decimal",
                            "dateFormat": "iso",
                        },
                    )
                except RuntimeError as exc:
                    if self.verbose:
                        print(f"[oddsapi] {key}: {exc}", file=sys.stderr)
                    continue
                if not isinstance(data, list):
                    continue
                for raw in data:
                    event = self._parse_event(raw, sport)
                    if event is None:
                        continue
                    events[event.id] = event
                    quotes.extend(self._parse_quotes(raw, event))

        if self.verbose and self.remaining is not None:
            print(f"[oddsapi] peticiones restantes: {self.remaining}", file=sys.stderr)
        return Snapshot(events=list(events.values()), quotes=quotes)

    def _parse_event(self, raw: dict, sport: str) -> Event | None:
        home = raw.get("home_team")
        away = raw.get("away_team")
        if not home or not away:
            return None
        return Event(
            id=str(raw.get("id")),
            sport=sport,
            league=raw.get("sport_title") or raw.get("sport_key") or "",
            home=home,
            away=away,
            commence=_parse_dt(raw.get("commence_time")),
        )

    def _parse_quotes(self, raw: dict, event: Event) -> list[Quote]:
        out: list[Quote] = []
        for book in raw.get("bookmakers", []) or []:
            book_key = book.get("key") or book.get("title") or "unknown"
            updated = _parse_dt(book.get("last_update"))
            for market in book.get("markets", []) or []:
                mkey = market.get("key")
                if mkey in ("h2h_1h", "h2h_ht"):
                    mkey = "h2h_ht"
                if mkey not in (
                    "h2h", "h2h_ht", "spreads", "totals", "btts",
                    "team_totals", "sets_home", "sets_away",
                ):
                    continue
                for outcome in market.get("outcomes", []) or []:
                    name = outcome.get("name") or outcome.get("description", "")
                    price = outcome.get("price")
                    if price is None or float(price) <= 1.0:
                        continue
                    point = outcome.get("point")
                    point = float(point) if point is not None else None

                    if mkey == "team_totals":
                        # Aquí ``name`` es el equipo y ``description`` es
                        # Over/Under. Lo normalizamos a team_totals_home/away.
                        side = self._as_outcome(
                            outcome.get("description", ""), "totals",
                            event.home, event.away,
                        )
                        if name.strip().lower() == event.home.strip().lower():
                            market_name = "team_totals_home"
                        elif name.strip().lower() == event.away.strip().lower():
                            market_name = "team_totals_away"
                        else:
                            continue
                        if side is None:
                            continue
                        out.append(
                            Quote(
                                event_id=event.id,
                                bookmaker=book_key,
                                market=market_name,
                                outcome=side,
                                price=float(price),
                                point=point,
                                captured_at=updated,
                            )
                        )
                        continue

                    mapped = self._as_outcome(name, mkey, event.home, event.away)
                    if mapped is None:
                        continue
                    if mkey in ("sets_home", "sets_away") and point is None:
                        point = 0.5
                    if mkey == "spreads" and point is not None:
                        # La API da el hándicap de cada equipo. Nosotros
                        # guardamos el hándicap del local como clave común,
                        # para que las dos selecciones caigan en el mismo
                        # mercado (spreads@-1.5).
                        if mapped == "away":
                            point = -point
                    out.append(
                        Quote(
                            event_id=event.id,
                            bookmaker=book_key,
                            market=mkey,
                            outcome=mapped,
                            price=float(price),
                            point=point,
                            captured_at=updated,
                        )
                    )
        return out
