"""Errores de casa de apuestas: cuotas desalineadas y arbitraje.

Aquí está la parte que pediste explícitamente: detectar "errores" de las casas.
Se detectan tres cosas distintas:

1. **Mispricing**: una casa ofrece una cuota claramente mejor que el consenso
   de mercado (ponderado hacia las casas sharps). Puede ser un error de
   digitación, una línea vieja, o simplemente una casa con otra opinión.

2. **Arbitraje**: apostando a todas las selecciones en la mejor casa de cada
   una, se obtiene beneficio sin riesgo. En la práctica las casas limitan o
   anulan este tipo de apuestas, así que es informativo y hay que actuar rápido.

3. **Divergencia**: las casas no se ponen de acuerdo (spread de cuotas amplio)
   aunque ninguna esté claramente por encima del consenso. Señal de mercado
   inestable o de información asimétrica.

Aviso importante: si una cuota parece un error evidente (por ejemplo, un
favorito a 15.00), la casa probablemente aplique su cláusula de *error
palpable* y anule la apuesta. Estas alertas sirven para investigar, no para
apostar a ciegas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .. import devig
from ..types import Event, Quote, Signal


@dataclass(frozen=True)
class ErrorConfig:
    mispricing_edge: float = 0.05     # 5% por encima del consenso
    divergence_spread: float = 0.12   # 12% de diferencia entre mejor y peor cuota
    min_books: int = 2
    method: str = "shin"
    sharp_books: tuple[str, ...] = (
        "pinnacle", "betfair", "betfair_ex_eu", "betfair_ex_uk", "smarkets", "matchbook",
    )


def _group(quotes: Sequence[Quote]) -> dict[str, dict[str, dict[str, float]]]:
    grouped: dict[str, dict[str, dict[str, float]]] = {}
    for q in quotes:
        grouped.setdefault(q.key, {}).setdefault(q.bookmaker, {})[q.outcome] = q.price
    return grouped


def detect_errors(
    event: Event,
    quotes: Sequence[Quote],
    config: ErrorConfig | None = None,
) -> list[Signal]:
    config = config or ErrorConfig()
    grouped = _group(quotes)
    signals: list[Signal] = []

    for key, prices in grouped.items():
        complete = {
            book: row
            for book, row in prices.items()
            if len(row) >= 2 and all(p > 1.0 for p in row.values())
        }
        if len(complete) < config.min_books:
            continue

        probs_by_book = devig.book_probabilities(complete, config.method)
        fair = devig.consensus(probs_by_book)
        if not fair:
            continue

        # --- Mispricing: cuota generosa frente al consenso -----------------
        flagged: set[tuple[str, str]] = set()
        for book, row in complete.items():
            for outcome, price in row.items():
                fair_prob = fair.get(outcome)
                if fair_prob is None or fair_prob <= 0:
                    continue
                edge = fair_prob * price - 1.0
                if edge < config.mispricing_edge:
                    continue
                is_sharp = book.lower() in config.sharp_books
                if is_sharp:
                    # Una sharp por encima del consenso es información, no error.
                    continue
                flagged.add((book, outcome))
                fair_price = 1.0 / fair_prob
                severity = 3 if edge >= 2 * config.mispricing_edge else 2
                signals.append(
                    Signal(
                        kind="MISPRICING",
                        sport=event.sport,
                        league=event.league,
                        match=event.label,
                        commence=event.commence,
                        market=key,
                        outcome=outcome,
                        bookmaker=book,
                        price=price,
                        fair_prob=fair_prob,
                        edge=edge,
                        detail=(
                            f"cuota {price:.2f} vs justa {fair_price:.2f} "
                            f"según consenso ({edge:+.1%})"
                        ),
                        severity=severity,
                        event_id=event.id,
                    )
                )

        # --- Arbitraje -----------------------------------------------------
        arb = devig.arbitrage(complete)
        if arb is not None:
            profit, best = arb
            main_outcome = max(best, key=lambda o: best[o][0])
            price, book = best[main_outcome]
            signals.append(
                Signal(
                    kind="ARB",
                    sport=event.sport,
                    league=event.league,
                    match=event.label,
                    commence=event.commence,
                    market=key,
                    outcome=" / ".join(
                        f"{o}@{p:.2f}({b})" for o, (p, b) in best.items()
                    ),
                    bookmaker=book,
                    price=price,
                    edge=profit,
                    detail=(
                        f"arbitraje {profit:+.2%}: "
                        + ", ".join(f"{o} {p:.2f} en {b}" for o, (p, b) in best.items())
                    ),
                    severity=3,
                    event_id=event.id,
                )
            )

        # --- Divergencia entre casas --------------------------------------
        for outcome in fair:
            prices_out = [
                row[outcome] for row in complete.values() if outcome in row
            ]
            if len(prices_out) < 2:
                continue
            best_price, best_book = max(
                ((row[outcome], book) for book, row in complete.items() if outcome in row),
                key=lambda x: x[0],
            )
            spread = best_price / min(prices_out) - 1.0
            if spread < config.divergence_spread:
                continue
            if (best_book, outcome) in flagged:
                continue  # ya hay un mispricing más específico
            if best_book.lower() in config.sharp_books:
                continue  # la sharp es la referencia, no el outlier
            signals.append(
                Signal(
                    kind="DIVERGENCE",
                    sport=event.sport,
                    league=event.league,
                    match=event.label,
                    commence=event.commence,
                    market=key,
                    outcome=outcome,
                    bookmaker=best_book,
                    price=best_price,
                    fair_prob=fair.get(outcome),
                    detail=(
                        f"mejor {best_price:.2f} ({best_book}) vs peor "
                        f"{min(prices_out):.2f} · spread {spread:+.1%}"
                    ),
                    severity=1,
                    event_id=event.id,
                )
            )

    signals.sort(key=lambda s: (s.severity, s.edge or 0.0), reverse=True)
    return signals
