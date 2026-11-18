"""Modelo de tarjetas.

Las tarjetas se modelan con Poisson, con tres multiplicadores sobre la media de
la liga:

* **Equipo**: unos equipos son mucho más tarjeteros que otros.
* **Árbitro**: el factor más importante y el más ignorado. ``event.extra``
  puede traer ``referee_factor`` (1.0 = medio, 1.3 = árbitro duro).
* **Contexto**: derbis y partidos de máxima tensión suben el ritmo de faltas.
  ``event.extra["derby"] = True`` aplica un +15%.

Con datos históricos (``home_cards``/``away_cards`` en los resultados) se
ajustan los factores por equipo. Sin datos, se usan priors de la liga.
"""

from __future__ import annotations

from typing import Sequence

from .. import leagues
from ..types import Event, Quote, market_key, split_key
from .base import MarketProbabilities
from .counts import over_prob_poisson

DEFAULT_CARDS = 4.2
DEFAULT_LINES = (3.5, 4.5, 5.5)


def _get(r, key, default=None):
    if isinstance(r, dict):
        return r.get(key, default)
    return getattr(r, key, default)


class CardsModel:
    name = "cards-poisson"

    def __init__(self, default_cards: float = DEFAULT_CARDS, derby_boost: float = 1.15):
        self.default_cards = default_cards
        self.derby_boost = derby_boost
        self.team_cards: dict[str, float] = {}
        self.league_mean: float = default_cards
        self.fitted = False

    # ------------------------------------------------------------------ fit
    def fit(self, results: Sequence) -> "CardsModel":
        totals: list[float] = []
        per_team: dict[str, list[float]] = {}
        for r in results:
            hc = _get(r, "home_cards")
            ac = _get(r, "away_cards")
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
        self.team_cards = {
            team: (sum(vals) / len(vals)) / half for team, vals in per_team.items()
        }
        self.fitted = True
        return self

    # -------------------------------------------------------------- predict
    def expected_cards(self, event: Event) -> float:
        profile = leagues.find(event.league)
        base = (
            profile.cards
            if profile is not None and profile.sport == "soccer" and profile.cards
            else self.league_mean or self.default_cards
        )
        f_home = self.team_cards.get(event.home, 1.0) if self.fitted else 1.0
        f_away = self.team_cards.get(event.away, 1.0) if self.fitted else 1.0
        lam = base * (f_home + f_away) / 2.0

        extra = event.extra or {}
        referee = extra.get("referee_factor")
        if referee is not None:
            lam *= float(referee)
        if extra.get("derby"):
            lam *= self.derby_boost
        return max(lam, 0.1)

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        lam = self.expected_cards(event)
        lines = sorted(
            {
                point
                for q in (quotes or [])
                for market, point in [split_key(q.key)]
                if market == "cards" and point is not None
            }
            or set(DEFAULT_LINES)
        )
        out: MarketProbabilities = {}
        for line in lines:
            over = over_prob_poisson(lam, line)
            out[market_key("cards", line)] = {"over": over, "under": 1.0 - over}
        return out
