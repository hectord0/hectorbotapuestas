"""Backtesting honesto del modelo de fútbol contra las cuotas del mercado.

La pregunta que responde: *¿el modelo bate al mercado o solo parece que sí?*

Se entrena con la parte antigua de los datos y se evalúa con la parte reciente,
nunca al revés. Se comparan dos cosas:

* **Log-loss** del modelo frente al log-loss de las probabilidades sin margen
  del mercado. Menos es mejor. Si el modelo no mejora al mercado, no hay edge.
* **ROI** apostando 1 unidad plana a todo lo que supere ``min_edge``.

Formato soportado: CSV de football-data.co.uk (columnas ``Div, Date, HomeTeam,
AwayTeam, FTHG, FTAG`` y cuotas ``PSCH/PSD/PSCA`` o ``B365H/B365D/B365A``).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import devig
from .models.football import FootballModel, MatchResult
from .types import Event

BOOKS = {
    "b365": ("B365H", "B365D", "B365A"),
    "pinnacle": ("PSCH", "PSD", "PSCA"),
    "pinnacle_open": ("PSH", "PSD", "PSA"),
    "bwin": ("BWH", "BWD", "BWA"),
    "avg": ("AvgH", "AvgD", "AvgA"),
    "max": ("MaxH", "MaxD", "MaxA"),
}
_FALLBACK_ORDER = ("b365", "pinnacle", "bwin", "avg", "max", "pinnacle_open")


@dataclass
class MatchRow:
    date: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    odds: tuple[float, float, float] | None = None

    @property
    def result(self) -> str:
        if self.home_goals > self.away_goals:
            return "home"
        if self.home_goals < self.away_goals:
            return "away"
        return "draw"


@dataclass
class BacktestReport:
    n_train: int = 0
    n_test: int = 0
    model_logloss: float = 0.0
    market_logloss: float = 0.0
    bets: int = 0
    staked: float = 0.0
    profit: float = 0.0
    wins: int = 0
    skipped_no_odds: int = 0
    book: str = "b365"
    kappa: float = 1.0

    @property
    def roi(self) -> float:
        return self.profit / self.staked if self.staked else 0.0

    @property
    def hit_rate(self) -> float:
        return self.wins / self.bets if self.bets else 0.0

    def summary(self) -> str:
        verdict = (
            "el modelo NO bate al mercado"
            if self.model_logloss >= self.market_logloss
            else "el modelo bate al mercado en log-loss"
        )
        lines = [
            f"Casa de referencia: {self.book} · calibración kappa={self.kappa:.2f}",
            f"Partidos: {self.n_train} entrenamiento / {self.n_test} test",
            f"Log-loss modelo: {self.model_logloss:.4f}",
            f"Log-loss mercado: {self.market_logloss:.4f}  ({verdict})",
            f"Apuestas (edge mínimo): {self.bets} · acierto {self.hit_rate:.1%}",
            f"Staked: {self.staked:.2f} · beneficio: {self.profit:+.2f}",
            f"ROI: {self.roi:+.2%}",
        ]
        if self.skipped_no_odds:
            lines.append(f"Sin cuotas comparables: {self.skipped_no_odds}")
        if self.bets < 50:
            lines.append("Aviso: menos de 50 apuestas, la muestra es demasiado pequeña para concluir nada.")
        return "\n".join(lines)


def _parse_date(raw: str) -> date | None:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(raw) -> float | None:
    try:
        value = float(str(raw).strip())
        return value if value > 1.0 else None
    except (TypeError, ValueError):
        return None


def load_csv(path: str | Path, book: str = "b365") -> list[MatchRow]:
    """Carga el CSV. ``book`` elige la casa de referencia para evaluar."""
    columns = BOOKS.get(book, BOOKS["b365"])
    order = [columns] + [
        BOOKS[b] for b in _FALLBACK_ORDER if b != book and b in BOOKS
    ]
    rows: list[MatchRow] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            d = _parse_date(raw.get("Date", ""))
            home = (raw.get("HomeTeam") or "").strip()
            away = (raw.get("AwayTeam") or "").strip()
            if d is None or not home or not away:
                continue
            try:
                hg = int(raw.get("FTHG"))
                ag = int(raw.get("FTAG"))
            except (TypeError, ValueError):
                continue
            odds = None
            for h_col, d_col, a_col in order:
                h, dr, a = (_to_float(raw.get(c)) for c in (h_col, d_col, a_col))
                if h and dr and a:
                    odds = (h, dr, a)
                    break
            rows.append(MatchRow(d, home, away, hg, ag, odds))
    rows.sort(key=lambda r: r.date)
    return rows


def evaluate(
    rows: list[MatchRow],
    min_edge: float = 0.03,
    train_ratio: float = 0.7,
    method: str = "shin",
    book: str = "b365",
    max_price: float | None = None,
) -> BacktestReport:
    rows = [r for r in rows if r.home != r.away]
    if len(rows) < 50:
        raise ValueError("hacen falta al menos 50 partidos para un backtest mínimamente serio")
    split = max(int(len(rows) * train_ratio), 1)
    train, test = rows[:split], rows[split:]

    model = FootballModel().fit(
        [MatchResult(r.date, r.home, r.away, r.home_goals, r.away_goals) for r in train]
    )

    report = BacktestReport(n_train=len(train), n_test=len(test))
    report.book = book
    report.kappa = getattr(model, "kappa", 1.0)
    ll_model = ll_market = 0.0
    n_with_odds = 0

    for r in test:
        event = Event(
            id=f"{r.home}-{r.away}-{r.date}",
            sport="soccer",
            league="",
            home=r.home,
            away=r.away,
            commence=datetime(r.date.year, r.date.month, r.date.day, tzinfo=timezone.utc),
        )
        probs = model.probabilities(event)["h2h"]
        actual = r.result
        ll_model += -math.log(max(probs.get(actual, 0.0), 1e-9))

        if not r.odds:
            report.skipped_no_odds += 1
            continue
        market = dict(zip(("home", "draw", "away"), devig.devig(r.odds, method)))
        ll_market += -math.log(max(market.get(actual, 0.0), 1e-9))
        n_with_odds += 1

        edges = {o: probs[o] * price - 1.0 for o, price in zip(("home", "draw", "away"), r.odds)}
        outcome, edge = max(edges.items(), key=lambda kv: kv[1])
        if edge < min_edge:
            continue
        price = r.odds[("home", "draw", "away").index(outcome)]
        if max_price is not None and price > max_price:
            continue
        report.bets += 1
        report.staked += 1.0
        if outcome == actual:
            report.wins += 1
            report.profit += price - 1.0
        else:
            report.profit -= 1.0

    report.model_logloss = ll_model / max(len(test), 1)
    report.market_logloss = ll_market / max(n_with_odds, 1) if n_with_odds else float("nan")
    return report
