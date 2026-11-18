"""Proveedor sintético para desarrollo y demo.

Genera eventos de los cinco deportes con probabilidades "verdaderas" conocidas,
y cuotas de varias casas con márgenes realistas, ruido entre casas, una cuota
errónea deliberada y una deriva temporal de precios para que el detector de
movimientos tenga algo que detectar.

Cubre todos los mercados que el bot analiza:

* fútbol: 1X2, goles en varias líneas (1.5/2.5/3.5), goles por equipo, BTTS.
* fútbol: tarjetas y córners con sus modelos de conteo.
* baloncesto: ganador y varias líneas de total.

Importante: es un *fixture*, no un simulador de mercado. Su utilidad es probar
la tubería completa (ingesta → devig → modelo → señales → alertas) sin gastar
peticiones de API ni depender de la red.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from ..models.cards import CardsModel
from ..models.corners import CornersModel
from ..models.tennis import at_least_one_set_prob
from ..types import Event, Quote, utcnow
from .base import Snapshot

# (nombre, margen, sigma del ruido log-normal)
BOOKS = [
    ("pinnacle", 0.020, 0.006),
    ("betfair", 0.030, 0.010),
    ("bet365", 0.055, 0.018),
    ("1xbet", 0.060, 0.020),
    ("williamhill", 0.070, 0.025),
    ("unibet", 0.065, 0.022),
]

FIXTURES = [
    ("soccer", "LaLiga", "Real Madrid", "Barcelona", 0.44, 0.26),
    ("soccer", "Premier League", "Arsenal", "Liverpool", 0.40, 0.27),
    ("soccer", "Serie A", "Inter", "Juventus", 0.42, 0.29),
    ("basketball", "NBA", "Lakers", "Celtics", 0.58, 0.0),
    ("basketball", "Euroliga", "Real Madrid BC", "Barça BC", 0.62, 0.0),
    ("tennis", "ATP Masters", "Alcaraz", "Sinner", 0.58, 0.0),
    ("tennis", "ATP Masters", "Djokovic", "Zverev", 0.64, 0.0),
    ("mma", "UFC", "Pereira", "Ankalaev", 0.53, 0.0),
    ("mma", "UFC", "Topuria", "Oliveira", 0.61, 0.0),
    ("table_tennis", "WTT", "Fan Zhendong", "Wang Chuqin", 0.66, 0.0),
    ("table_tennis", "WTT", "Harimoto", "Lin Yun-Ju", 0.55, 0.0),
]

SOCCER_TOTALS = (1.5, 2.5, 3.5)
BASKETBALL_TOTALS = (210.5, 215.5, 220.5, 225.5, 230.5)


def _poisson_matrix(lh: float, la: float, n: int = 9) -> list[list[float]]:
    def pmf(lam, k):
        return math.exp(-lam) * lam**k / math.factorial(k)

    m = [[pmf(lh, x) * pmf(la, y) for y in range(n)] for x in range(n)]
    total = sum(sum(row) for row in m)
    return [[v / total for v in row] for row in m]


def _over_total(m: list[list[float]], line: float) -> float:
    n = len(m)
    return sum(
        m[x][y] for x in range(n) for y in range(n) if x + y > line
    )


def _over_home(m: list[list[float]], line: float) -> float:
    return sum(sum(row) for x, row in enumerate(m) if x > line)


def _soccer_probs(p_home: float, p_draw: float) -> dict[str, dict[str, float]]:
    p_away = 1.0 - p_home - p_draw
    # λ coherentes con las probabilidades dadas (aproximación razonable).
    lh = 1.30 + 1.6 * (p_home - 0.40)
    la = 1.30 - 1.6 * (p_home - 0.40) * 0.8
    lh, la = max(lh, 0.4), max(la, 0.4)
    m = _poisson_matrix(lh, la)
    probs: dict[str, dict[str, float]] = {
        "h2h": {"home": p_home, "draw": p_draw, "away": p_away},
    }
    half = _poisson_matrix(lh / 2.0, la / 2.0)
    ht_home = sum(half[x][y] for x in range(len(half)) for y in range(len(half)) if x > y)
    ht_draw = sum(half[i][i] for i in range(len(half)))
    ht_away = 1.0 - ht_home - ht_draw
    probs["h2h_ht"] = {"home": ht_home, "draw": ht_draw, "away": ht_away}
    for line in SOCCER_TOTALS:
        over = _over_total(m, line)
        probs[f"totals@{line:g}"] = {"over": over, "under": 1.0 - over}
    for line in (0.5, 1.5):
        over = _over_home(m, line)
        probs[f"team_totals_home@{line:g}"] = {"over": over, "under": 1.0 - over}
    btts = sum(m[x][y] for x in range(1, len(m)) for y in range(1, len(m)))
    probs["btts"] = {"yes": btts, "no": 1.0 - btts}
    return probs


def _basketball_probs(p_home: float, mean_total: float = 220.0, sigma: float = 18.0):
    probs: dict[str, dict[str, float]] = {
        "h2h": {"home": p_home, "away": 1.0 - p_home},
    }
    for line in BASKETBALL_TOTALS:
        z = (line - mean_total) / sigma
        over = 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        probs[f"totals@{line:g}"] = {"over": over, "under": 1.0 - over}
    return probs


class SampleProvider:
    name = "sample"

    def __init__(
        self,
        seed: int = 11,
        steps: int = 4,
        interval_min: int = 20,
    ) -> None:
        self.rng = random.Random(seed)
        self.steps = steps
        self.interval_min = interval_min
        self.step = 0
        self.base_time = utcnow() - timedelta(minutes=interval_min * (steps - 1))
        self.events: list[Event] = []
        self.truth: dict[str, dict[str, dict[str, float]]] = {}
        self._noise: dict[tuple, float] = {}
        self._base_truth: dict[str, dict[str, dict[str, float]]] = {}
        self._build()

    def _build(self) -> None:
        cards_model = CardsModel()
        corners_model = CornersModel()
        for idx, (sport, league, home, away, p_home, p_draw) in enumerate(FIXTURES):
            event = Event(
                id=f"sample-{sport}-{idx}",
                sport=sport,
                league=league,
                home=home,
                away=away,
                commence=utcnow() + timedelta(hours=6 + idx),
            )
            self.events.append(event)
            if sport == "soccer":
                probs = _soccer_probs(p_home, p_draw)
                # Tarjetas y córners con los modelos de conteo reales, para que
                # el fixture ejercite exactamente el mismo camino que producción.
                probs.update(cards_model.probabilities(event))
                probs.update(corners_model.probabilities(event))
            elif sport == "basketball":
                probs = _basketball_probs(p_home)
            else:
                probs = {"h2h": {"home": p_home, "away": 1 - p_home}}
                if sport == "tennis":
                    home_set = at_least_one_set_prob(p_home)
                    away_set = at_least_one_set_prob(1 - p_home)
                    probs["sets_home@0.5"] = {
                        "over": home_set, "under": 1 - home_set,
                    }
                    probs["sets_away@0.5"] = {
                        "over": away_set, "under": 1 - away_set,
                    }
            self._base_truth[event.id] = probs

            for key, outcomes in probs.items():
                for book, _margin, sigma in BOOKS:
                    for outcome in outcomes:
                        self._noise[(event.id, key, outcome, book)] = math.exp(
                            self.rng.gauss(0.0, sigma)
                        )
        self.truth = {
            eid: {k: dict(v) for k, v in probs.items()}
            for eid, probs in self._base_truth.items()
        }

    def _drift(self, event_id: str, key: str, outcome: str, step: int) -> float:
        """Mueve las probabilidades con el tiempo para simular steam/drift."""
        if key != "h2h" or step == 0:
            return 0.0
        first_soccer = "sample-soccer-0"
        if event_id == first_soccer and outcome == "home":
            return 0.030 * step          # dinero entrando al local
        if event_id == "sample-tennis-5" and outcome == "away":
            return 0.018 * step          # dinero contra el visitante
        return 0.0

    def _probabilities(self, event: Event, step: int) -> dict[str, dict[str, float]]:
        probs = {k: dict(v) for k, v in self._base_truth[event.id].items()}
        row = probs.get("h2h")
        if row:
            shift = self._drift(event.id, "h2h", "home", step)
            if shift:
                old_home = row["home"]
                new_home = min(old_home + shift, 0.97)
                scale = (1.0 - new_home) / max(1.0 - old_home, 1e-9)
                row["home"] = new_home
                for other in list(row):
                    if other != "home":
                        row[other] = row[other] * scale
        return probs

    def _deliberate_error(self, event_id: str, book: str, key: str, outcome: str) -> bool:
        """Cuotas erróneas plantadas para que el demo tenga algo que cazar."""
        if book != "bet365":
            return False
        # Fútbol 1: visitante pagado de más (error de gama baja).
        if event_id == "sample-soccer-0" and key == "h2h" and outcome == "away":
            return True
        # Fútbol 3: córners under de una línea pagados de más (error raro).
        if event_id == "sample-soccer-2" and key == "corners@10.5" and outcome == "under":
            return True
        # 1ª parte y sets: ejemplos de mercados avanzados.
        if event_id == "sample-soccer-1" and key == "h2h_ht" and outcome == "home":
            return True
        if event_id == "sample-tennis-5" and key == "sets_home@0.5" and outcome == "over":
            return True
        return False

    def fetch(self, sports) -> Snapshot:
        as_of = self.base_time + timedelta(minutes=self.interval_min * self.step)
        step = self.step
        self.step += 1

        selected = [e for e in self.events if e.sport in sports]
        quotes: list[Quote] = []
        for event in selected:
            probs = self._probabilities(event, step)
            self.truth[event.id] = probs
            for key, outcomes in probs.items():
                for book, margin, _sigma in BOOKS:
                    raw = {
                        o: max(p, 1e-4) * self._noise[(event.id, key, o, book)]
                        for o, p in outcomes.items()
                    }
                    total = sum(raw.values())
                    normalised = {o: v / total for o, v in raw.items()}
                    # Error deliberado: la casa paga de más esa selección.
                    for outcome in list(normalised):
                        if self._deliberate_error(event.id, book, key, outcome):
                            normalised[outcome] *= 0.80
                    for outcome, p in normalised.items():
                        price = round(1.0 / max(p * (1.0 + margin), 1e-6), 2)
                        if price <= 1.01:
                            price = 1.01
                        quotes.append(
                            Quote(
                                event_id=event.id,
                                bookmaker=book,
                                market=key.partition("@")[0],
                                outcome=outcome,
                                price=price,
                                point=(
                                    float(key.partition("@")[2])
                                    if "@" in key
                                    else None
                                ),
                                captured_at=as_of,
                            )
                        )
        return Snapshot(events=selected, quotes=quotes)


class SampleTruthModel:
    """Modelo de utilería: devuelve las probabilidades verdaderas del fixture.

    Sirve para demostrar la detección de value. En producción lo sustituye el
    modelo estadístico real de cada deporte.
    """

    name = "sample-truth"

    def __init__(self, provider: SampleProvider) -> None:
        self.provider = provider

    def probabilities(self, event: Event, quotes=None):
        return self.provider.truth.get(event.id, {})
