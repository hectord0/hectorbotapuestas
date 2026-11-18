"""Eliminación del margen de las casas de apuestas ("devigging").

Una cuota decimal ``o`` implica una probabilidad bruta ``1/o``. La suma de esas
probabilidades en un mercado siempre supera 1: ese exceso es el margen
(overround) con el que la casa gana dinero. Para comparar casas y detectar
errores necesitamos estimar la probabilidad *justa*, sin margen.

Implementamos cuatro métodos, de simple a sofisticado:

* ``multiplicative``: reparte el margen en proporción a la probabilidad. Es el
  estándar de facto, pero regala demasiada probabilidad a los longshots.
* ``additive``: resta el mismo margen a todas las selecciones. Sesga los
  longshots hacia abajo (puede dar negativos, que recortamos).
* ``power``: busca un exponente ``k`` tal que ``sum(p_i ** k) == 1``. Con
  ``k > 1`` encoge más los longshots, que es justo el sesgo favorito-longshot
  que queremos corregir.
* ``shin``: modelo de Shin (1993), asume que parte del margen existe porque
  hay apostadores con información privilegiada. Es el más usado por los
  analistas de cuotas y el que mejor recupera probabilidades reales.

Referencias: Shin (1993), "Measuring the Incidence of Insider Trading in a
Market for State-Contingent Claims"; Joseph Buchdahl, "Squares and Sharps".
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, Mapping, Sequence

METHODS = ("multiplicative", "additive", "power", "shin")


def implied_probs(odds: Sequence[float]) -> list[float]:
    return [1.0 / o for o in odds]


def overround(odds: Sequence[float]) -> float:
    """Margen de la casa: ``0.05`` significa un 5% de overround."""
    return sum(1.0 / o for o in odds) - 1.0


def multiplicative(odds: Sequence[float]) -> list[float]:
    inv = implied_probs(odds)
    total = sum(inv)
    if total <= 0:
        raise ValueError("cuotas inválidas")
    return [p / total for p in inv]


def additive(odds: Sequence[float]) -> list[float]:
    inv = implied_probs(odds)
    total = sum(inv)
    n = len(inv)
    margin = (total - 1.0) / n
    out = [p - margin for p in inv]
    if any(p <= 0 for p in out):
        # Con márgenes altos el método aditivo rompe los longshots; recortamos
        # a un mínimo y renormalizamos para no devolver probabilidades negativas.
        out = [max(p, 1e-9) for p in out]
        s = sum(out)
        out = [p / s for p in out]
    return out


def power(odds: Sequence[float]) -> list[float]:
    inv = implied_probs(odds)
    total = sum(inv)
    if total <= 1.0:
        return [p / total for p in inv]
    if len(inv) == 1:
        return [1.0]

    def excess(k: float) -> float:
        return sum(p ** k for p in inv) - 1.0

    lo, hi = 1.0, 100.0
    while excess(hi) > 0 and hi < 1e6:
        hi *= 2.0
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if excess(mid) > 0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    out = [p ** k for p in inv]
    s = sum(out)
    return [p / s for p in out]


def shin(odds: Sequence[float]) -> list[float]:
    """Método de Shin. Resuelve ``z`` (proporción de insider) por bisección."""
    inv = implied_probs(odds)
    booksum = sum(inv)
    if booksum <= 1.0:
        return [p / booksum for p in inv]
    if len(inv) == 1:
        return [1.0]

    def total(z: float) -> float:
        return sum(
            (math.sqrt(z * z + 4.0 * (1.0 - z) * (p * p) / booksum) - z)
            / (2.0 * (1.0 - z))
            for p in inv
        )

    lo, hi = 0.0, 0.9999
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    z = (lo + hi) / 2.0
    out = [
        (math.sqrt(z * z + 4.0 * (1.0 - z) * (p * p) / booksum) - z)
        / (2.0 * (1.0 - z))
        for p in inv
    ]
    s = sum(out)
    return [p / s for p in out]


def devig(odds: Sequence[float], method: str = "shin") -> list[float]:
    """Devuelve probabilidades justas (suman 1) para una lista de cuotas."""
    if method not in METHODS:
        raise ValueError(f"método desconocido: {method!r}; usa uno de {METHODS}")
    if any(o <= 1.0 for o in odds):
        raise ValueError(f"cuota decimal inválida en {odds!r} (debe ser > 1)")
    return {
        "multiplicative": multiplicative,
        "additive": additive,
        "power": power,
        "shin": shin,
    }[method](odds)


def book_probabilities(
    prices: Mapping[str, Mapping[str, float]], method: str = "shin"
) -> dict[str, dict[str, float]]:
    """Convierte ``{casa: {selección: cuota}}`` en ``{casa: {selección: prob}}``.

    Las casas con mercados incompletos o cuotas inválidas se descartan.
    """
    out: dict[str, dict[str, float]] = {}
    for book, row in prices.items():
        outcomes = list(row.keys())
        if len(outcomes) < 2:
            continue
        if any(row[o] <= 1.0 for o in outcomes):
            continue
        probs = devig([row[o] for o in outcomes], method)
        out[book] = dict(zip(outcomes, probs))
    return out


DEFAULT_SHARPNESS = {
    "pinnacle": 1.0,
    "betfair": 0.95,
    "betfair_ex_eu": 0.95,
    "betfair_ex_uk": 0.95,
    "smarkets": 0.9,
    "matchbook": 0.9,
    "1xbet": 0.6,
    "bet365": 0.55,
    "williamhill": 0.5,
    "unibet": 0.45,
    "betway": 0.4,
    "betsson": 0.4,
    "marathonbet": 0.5,
    "bwin": 0.45,
}


def sharpness(book: str, overrides: Mapping[str, float] | None = None) -> float:
    if overrides and book in overrides:
        return overrides[book]
    return DEFAULT_SHARPNESS.get(book.lower(), 0.35)


def consensus(
    probs_by_book: Mapping[str, Mapping[str, float]],
    overrides: Mapping[str, float] | None = None,
    method: str = "shin",
) -> dict[str, float]:
    """Probabilidad consenso: media ponderada por la fiabilidad de cada casa.

    Las casas sharps (Pinnacle) pesan mucho más porque son las que mueven el
    mercado y las que menos margen tienen.
    """
    if not probs_by_book:
        return {}
    outcomes = list(next(iter(probs_by_book.values())).keys())
    # Solo probs consistentes en todas las casas completas.
    usable = {
        b: p for b, p in probs_by_book.items() if sorted(p) == sorted(outcomes)
    }
    if not usable:
        return {}
    total_w = 0.0
    acc = {o: 0.0 for o in outcomes}
    for book, probs in usable.items():
        w = max(sharpness(book, overrides), 0.05)
        # Pesar también por ausencia de margen: menos margen = más informativo.
        w /= 1.0 + 0.5 * 0.0
        total_w += w
        for o in outcomes:
            acc[o] += w * probs[o]
    if total_w == 0:
        return {}
    out = {o: acc[o] / total_w for o in outcomes}
    s = sum(out.values())
    return {o: p / s for o, p in out.items()} if s > 0 else out


def best_prices(
    prices: Mapping[str, Mapping[str, float]]
) -> dict[str, tuple[float, str]]:
    """Mejor cuota disponible por selección: ``{selección: (cuota, casa)}``."""
    best: dict[str, tuple[float, str]] = {}
    for book, row in prices.items():
        for outcome, price in row.items():
            current = best.get(outcome)
            if current is None or price > current[0]:
                best[outcome] = (price, book)
    return best


def arbitrage(prices: Mapping[str, Mapping[str, float]]) -> tuple[float, dict[str, tuple[float, str]]] | None:
    """Devuelve ``(beneficio, mejores_cuotas)`` si hay arbitraje, si no ``None``.

    Arbitraje: apostando a todas las selecciones en la mejor casa de cada una,
    la suma de probabilidades implícitas es menor que 1.
    """
    best = best_prices(prices)
    if len(best) < 2:
        return None
    inv_sum = sum(1.0 / price for price, _ in best.values())
    if inv_sum >= 1.0:
        return None
    return (1.0 / inv_sum - 1.0, best)
