"""Distribuciones de conteo para goles, tarjetas y córners.

* **Poisson**: eventos raros e independientes. Vale para goles y tarjetas.
* **Binomial negativa**: como Poisson pero con más varianza que la media.
  Los córners están claramente sobredispersos (un partido puede tener 4 o 20),
  así que Poisson subestima las colas y la binomial negativa es más honesta.

Todo en Python puro, sin numpy.
"""

from __future__ import annotations

import math


def poisson_pmf(lam: float, k: int) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


def poisson_cdf(lam: float, k: int) -> float:
    if k < 0:
        return 0.0
    return sum(poisson_pmf(lam, i) for i in range(k + 1))


def negbinom_pmf(mean: float, size: float, k: int) -> float:
    """Binomial negativa parametrizada por media y tamaño (dispersion).

    ``size`` grande se acerca a Poisson; ``size`` pequeño = más dispersión.
    """
    if mean <= 0:
        return 1.0 if k == 0 else 0.0
    if size <= 0:
        return poisson_pmf(mean, k)
    p = size / (size + mean)
    # lgamma para estabilidad numérica con k grande
    log_pmf = (
        math.lgamma(k + size)
        - math.lgamma(size)
        - math.lgamma(k + 1)
        + size * math.log(p)
        + k * math.log(1.0 - p)
    )
    return math.exp(log_pmf)


def negbinom_cdf(mean: float, size: float, k: int) -> float:
    if k < 0:
        return 0.0
    return sum(negbinom_pmf(mean, size, i) for i in range(k + 1))


def over_prob_poisson(lam: float, line: float) -> float:
    """P(total > line) con líneas .5 (sin empate posible)."""
    return max(0.0, min(1.0, 1.0 - poisson_cdf(lam, math.floor(line))))


def over_prob_negbinom(mean: float, size: float, line: float) -> float:
    return max(0.0, min(1.0, 1.0 - negbinom_cdf(mean, size, math.floor(line))))
