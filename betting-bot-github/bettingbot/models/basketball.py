"""Baloncesto: Elo con margen de victoria + línea base de totals."""

from __future__ import annotations

from .ratings import EloWithTotals


class BasketballModel(EloWithTotals):
    """Defaults pensados para NBA/Euroliga.

    * ``k=20``: cada partido mueve el rating como mucho ~20 puntos.
    * ``home_advantage=90``: la ventaja de cancha ronda 3-3.5 puntos, y en
      baloncesto 1 punto de spread ~ 25-30 puntos Elo.
    * ``mov=True``: ganar por 25 no es lo mismo que ganar por 2.
    * ``sigma=18``: desviación típica realista del total de puntos en NBA.
    """

    name = "basketball-elo"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("k", 20.0)
        kwargs.setdefault("scale", 400.0)
        kwargs.setdefault("home_advantage", 90.0)
        kwargs.setdefault("mov", True)
        kwargs.setdefault("sigma", 18.0)
        super().__init__(**kwargs)
