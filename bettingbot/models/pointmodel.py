"""Modelos de punto -> juego -> set -> partido.

Se usa para tenis (puntos a 4, sets a 6 juegos, tiebreak a 7) y para tenis de
mesa (puntos a 11, al mejor de 5 o 7 juegos). La matemática es la misma: una
cadena de Markov donde cada punto se gana con probabilidad constante al saque.

Fuente: Barnett & Clarke (2005), "Combining player statistics to predict
outcomes of tennis matches".
"""

from __future__ import annotations

from functools import lru_cache


def game_win_prob(p: float) -> float:
    """Probabilidad de ganar un juego al saque, ganando cada punto con prob ``p``.

    Fórmula cerrada clásica: victorias 4-0, 4-1, 4-2 y el estado de deuce
    resuelto como serie geométrica.
    """
    if not 0.0 <= p <= 1.0:
        raise ValueError("p debe estar en [0, 1]")
    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    q = 1.0 - p
    straight = p**4 * (1.0 + 4.0 * q + 10.0 * q * q)
    deuce_reach = 20.0 * p**3 * q**3
    deuce_win = p * p / (1.0 - 2.0 * p * q)
    return straight + deuce_reach * deuce_win


def race_win_prob(
    p_a: float,
    p_b: float,
    target: int = 7,
    win_by: int = 2,
    first_block: int = 1,
    block: int = 2,
    deuce_single_serve: bool = False,
) -> float:
    """Probabilidad de que A gane un "race to target" con saque por bloques.

    ``p_a``: prob. de que A gane un punto cuando saca A.
    ``p_b``: prob. de que B gane un punto cuando saca B.
    ``first_block``: puntos que saca A al inicio (1 en tiebreak de tenis,
    2 en tenis de mesa). Después se alternan bloques de ``block`` puntos.
    ``deuce_single_serve``: si es True, desde ``target-1`` iguales el saque
    alterna cada punto (regla real del tenis de mesa a 10-10).
    """

    def base_server_is_a(n: int) -> bool:
        # n es el punto 1-indexado.
        if n <= first_block:
            return True
        k = (n - first_block - 1) // block
        return k % 2 == 1

    deuce_start = 2 * (target - 1)
    first_deuce_point = deuce_start + 1
    anchor = base_server_is_a(first_deuce_point)

    def server_is_a(n: int) -> bool:
        if n <= deuce_start or not deuce_single_serve:
            return base_server_is_a(n)
        return anchor if (n - first_deuce_point) % 2 == 0 else not anchor

    @lru_cache(maxsize=None)
    def rec(a: int, b: int) -> float:
        if a >= target and a - b >= win_by:
            return 1.0
        if b >= target and b - a >= win_by:
            return 0.0
        # Zona de "deuce": empatados en target-1 o más. Se resuelve en forma
        # cerrada porque si no la recursión es infinita (8-8, 9-9, 10-10...).
        # Desde un empate, tras dos puntos o gana alguien por 2 o se vuelve al
        # mismo estado, así que P = P(AA) / (P(AA) + P(BB)).
        if a == b and a >= target - 1:
            n1 = a + b + 1
            p1 = p_a if server_is_a(n1) else (1.0 - p_b)
            p2 = p_a if server_is_a(n1 + 1) else (1.0 - p_b)
            win_two = p1 * p2
            lose_two = (1.0 - p1) * (1.0 - p2)
            denom = win_two + lose_two
            return win_two / denom if denom > 0 else 0.5
        n = a + b + 1
        p_point = p_a if server_is_a(n) else (1.0 - p_b)
        return p_point * rec(a + 1, b) + (1.0 - p_point) * rec(a, b + 1)

    return rec(0, 0)


def tiebreak_win_prob(p_a: float, p_b: float, target: int = 7) -> float:
    """Tiebreak de tenis: A saca 1 punto, luego bloques de 2, a ``target``."""
    return race_win_prob(p_a, p_b, target=target, win_by=2, first_block=1, block=2)


def set_win_prob(
    p_a: float,
    p_b: float,
    target_games: int = 6,
    first_server_is_a: bool = True,
) -> float:
    """Probabilidad de que A gane un set, a partir de probabilidades de punto.

    A 6-6 se juega tiebreak. El saque alterna juego a juego.
    """
    hold_a = game_win_prob(p_a)
    hold_b = game_win_prob(p_b)
    tb = tiebreak_win_prob(p_a, p_b)
    need = target_games

    @lru_cache(maxsize=None)
    def rec(games_a: int, games_b: int) -> float:
        if games_a >= need and games_a - games_b >= 2:
            return 1.0
        if games_b >= need and games_b - games_a >= 2:
            return 0.0
        if games_a == need and games_b == need:
            return tb
        played = games_a + games_b
        a_serves = (played % 2 == 0) if first_server_is_a else (played % 2 == 1)
        # Si saca A, gana el juego con hold_a. Si saca B, A gana el juego
        # rompiendo el saque: probabilidad 1 - hold_b.
        p_a_game = hold_a if a_serves else (1.0 - hold_b)
        return p_a_game * rec(games_a + 1, games_b) + (1.0 - p_a_game) * rec(
            games_a, games_b + 1
        )

    return rec(0, 0)


def match_win_prob(
    p_a: float,
    p_b: float,
    best_of: int = 3,
    target_games: int = 6,
) -> float:
    """Probabilidad de que A gane el partido al mejor de ``best_of`` sets.

    El saque inicial alterna de set en set.
    """
    if best_of not in (1, 3, 5):
        raise ValueError("best_of debe ser 1, 3 o 5")
    need = best_of // 2 + 1
    set_if_a_first = set_win_prob(p_a, p_b, target_games, True)
    set_if_b_first = set_win_prob(p_a, p_b, target_games, False)

    @lru_cache(maxsize=None)
    def rec(sets_a: int, sets_b: int) -> float:
        if sets_a >= need:
            return 1.0
        if sets_b >= need:
            return 0.0
        played = sets_a + sets_b
        p_set = set_if_a_first if played % 2 == 0 else set_if_b_first
        return p_set * rec(sets_a + 1, sets_b) + (1.0 - p_set) * rec(
            sets_a, sets_b + 1
        )

    return rec(0, 0)


def table_tennis_match_prob(p_a: float, p_b: float, best_of: int = 5) -> float:
    """Tenis de mesa: juegos a 11 puntos (win by 2), al mejor de ``best_of``.

    El saque alterna cada 2 puntos y, a 10-10, cada punto. El primer sacador
    alterna entre juegos.
    """
    if best_of not in (3, 5, 7):
        raise ValueError("best_of debe ser 3, 5 o 7")
    need = best_of // 2 + 1

    def game(first_server_is_a: bool) -> float:
        if first_server_is_a:
            return race_win_prob(
                p_a, p_b, target=11, win_by=2, first_block=2, block=2,
                deuce_single_serve=True,
            )
        return 1.0 - race_win_prob(
            p_b, p_a, target=11, win_by=2, first_block=2, block=2,
            deuce_single_serve=True,
        )

    p_game_a_first = game(True)
    p_game_b_first = game(False)

    @lru_cache(maxsize=None)
    def rec(games_a: int, games_b: int) -> float:
        if games_a >= need:
            return 1.0
        if games_b >= need:
            return 0.0
        played = games_a + games_b
        p_game = p_game_a_first if played % 2 == 0 else p_game_b_first
        return p_game * rec(games_a + 1, games_b) + (1.0 - p_game) * rec(
            games_a, games_b + 1
        )

    return rec(0, 0)


def from_point_probability(
    p_a: float,
    p_b: float,
    best_of: int = 3,
    target_games: int = 6,
) -> float:
    """Atajo legible: de probabilidad de punto al saque a ganar el partido."""
    return match_win_prob(p_a, p_b, best_of=best_of, target_games=target_games)
