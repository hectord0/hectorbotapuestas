"""Detección de value bets y sizing por Kelly fraccional.

Un value bet existe cuando la probabilidad del modelo, multiplicada por la
mejor cuota disponible, supera 1::

    edge = p_modelo * cuota - 1

Si el modelo dice 45% y la cuota es 2.50, el edge es ``0.45*2.50-1 = +12.5%``.

El sizing usa Kelly fraccional (por defecto 25% de Kelly completo), que es el
compromiso habitual entre crecer la banca y sobrevivir a rachas malas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .. import devig
from ..types import Event, Quote, Signal


@dataclass(frozen=True)
class ValueConfig:
    min_edge: float = 0.03          # 3% mínimo para avisar
    kelly_fraction: float = 0.25    # fracción de Kelly completo
    max_stake_pct: float = 0.02     # nunca más del 2% de la banca por apuesta
    bankroll: float = 1000.0
    min_price: float = 1.30
    max_price: float = 12.00
    # Si el modelo se separa del consenso sharp más que esto, sospechamos del
    # modelo (o de una cuota errónea) y no alertamos.
    max_model_disagreement: float = 0.20


def kelly_stake(prob: float, price: float, config: ValueConfig) -> float:
    """Importe sugerido en unidades monetarias (0 si no hay ventaja)."""
    if price <= 1.0 or not 0.0 < prob < 1.0:
        return 0.0
    b = price - 1.0
    full_kelly = (prob * price - 1.0) / b
    if full_kelly <= 0:
        return 0.0
    fraction = min(full_kelly * config.kelly_fraction, config.max_stake_pct)
    return round(config.bankroll * fraction, 2)


def group_by_market(quotes: Sequence[Quote]) -> dict[str, dict[str, dict[str, float]]]:
    """``{clave_mercado: {casa: {selección: cuota}}}``."""
    grouped: dict[str, dict[str, dict[str, float]]] = {}
    for q in quotes:
        grouped.setdefault(q.key, {}).setdefault(q.bookmaker, {})[q.outcome] = q.price
    return grouped


def find_value_bets(
    event: Event,
    quotes: Sequence[Quote],
    model_probs: Mapping[str, Mapping[str, float]],
    config: ValueConfig | None = None,
    sharp_consensus: Mapping[str, Mapping[str, float]] | None = None,
) -> list[Signal]:
    """Compara cada cuota con la probabilidad del modelo y devuelve las señales."""
    config = config or ValueConfig()
    grouped = group_by_market(quotes)
    signals: list[Signal] = []

    for key, outcome_probs in model_probs.items():
        prices = grouped.get(key)
        if not prices:
            continue
        best = devig.best_prices(prices)
        for outcome, p_model in outcome_probs.items():
            if outcome not in best:
                continue
            price, book = best[outcome]
            if not (config.min_price <= price <= config.max_price):
                continue
            edge = p_model * price - 1.0
            if edge < config.min_edge:
                continue
            if sharp_consensus is not None:
                fair = sharp_consensus.get(key, {}).get(outcome)
                if fair is not None and (p_model - fair) > config.max_model_disagreement:
                    continue
            stake = kelly_stake(p_model, price, config)
            if stake <= 0:
                continue
            signals.append(
                Signal(
                    kind="VALUE",
                    sport=event.sport,
                    league=event.league,
                    match=event.label,
                    commence=event.commence,
                    market=key,
                    outcome=outcome,
                    bookmaker=book,
                    price=price,
                    fair_prob=p_model,
                    edge=edge,
                    stake=stake,
                    detail=(
                        f"modelo {p_model:.1%} vs cuota {price:.2f} "
                        f"(justa {1/p_model:.2f}) · Kelly {config.kelly_fraction:.0%}"
                    ),
                    severity=3 if edge >= 2 * config.min_edge else 2,
                    event_id=event.id,
                )
            )

    signals.sort(key=lambda s: (s.edge or 0.0), reverse=True)
    return signals
