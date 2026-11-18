"""Fútbol: modelo Dixon-Coles con ajuste por máxima verosimilitud.

El modelo Poisson bivariado de Dixon-Coles (1997) es el estándar académico
para predecir resultados de fútbol. Cada equipo tiene una fuerza de ataque y
una debilidad defensiva; los goles de cada lado son Poisson con media::

    lambda_local  = exp(mu + ataque_local - defensa_visitante + ventaja_local)
    lambda_visit  = exp(mu + ataque_visit - defensa_local)

y se corrige la dependencia en resultados de pocos goles (0-0, 1-0, 0-1, 1-1)
con el factor ``tau`` y el parámetro ``rho``.

El ajuste se hace maximizando la log-verosimilitud ponderada por el tiempo
(los partidos viejos pesan menos) con un optimizador Adam, sin dependencias
externas. Incluye regularización L2 para que equipos con pocos partidos no
salgan con fuerzas absurdas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from .. import leagues
from ..types import Event, Quote, market_key, split_key
from .base import MarketProbabilities


@dataclass(frozen=True)
class MatchResult:
    date: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    league: str = ""


class HalfTimeModel:
    """Mercado de la 1ª parte usando solo el prior de goles de la liga.

    No necesita histórico: sirve para el proveedor manual y la API, donde no
    hay un Dixon-Coles ajustado. Con un modelo ajustado, ``FootballModel``
    gana porque usa fuerzas reales de los equipos.
    """

    name = "half-time-prior"

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        profile = leagues.find(event.league)
        goals = profile.goals if profile else 2.6
        lh = goals * 0.55 / 2.0
        la = goals * 0.45 / 2.0
        return {"h2h_ht": FootballModel._half_time_probs(lh, la)}


def _poisson_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


class FootballModel:
    name = "dixon-coles"

    def __init__(
        self,
        xi: float = 0.0018,
        ridge: float = 0.20,
        max_goals: int = 10,
        iterations: int = 900,
        lr: float = 0.5,
    ) -> None:
        self.xi = xi
        self.ridge = ridge
        self.max_goals = max_goals
        self.iterations = iterations
        self.lr = lr
        self.mu = 0.0
        self.home_adv = 0.0
        self.rho = 0.0
        self.attack: dict[str, float] = {}
        self.defence: dict[str, float] = {}
        self.fitted = False
        self.n_matches = 0
        # Exponente de calibración: p' ∝ p**kappa. Se ajusta en entrenamiento
        # para corregir la sobreconfianza típica del Poisson.
        self.kappa = 1.0

    # ------------------------------------------------------------------ fit
    def fit(self, results: Sequence[MatchResult]) -> "FootballModel":
        results = [r for r in results if r.home != r.away]
        if not results:
            raise ValueError("no hay partidos para ajustar el modelo")
        self.n_matches = len(results)

        teams = sorted({r.home for r in results} | {r.away for r in results})
        att = {t: 0.0 for t in teams}
        dfn = {t: 0.0 for t in teams}
        ref = max(r.date for r in results)
        weights = [
            math.exp(-self.xi * max((ref - r.date).days, 0)) for r in results
        ]
        total_weight = sum(weights) or 1.0
        mean_goals = (
            sum(w * (r.home_goals + r.away_goals) for w, r in zip(weights, results))
            / total_weight
            / 2.0
        )
        mu = math.log(max(mean_goals, 0.1))
        home_adv = 0.20

        def lambdas(r, p_mu, p_ha, p_att, p_dfn):
            eta_h = p_mu + p_att[r.home] - p_dfn[r.away] + p_ha
            eta_a = p_mu + p_att[r.away] - p_dfn[r.home]
            lh = math.exp(max(min(eta_h, 6.0), -6.0))
            la = math.exp(max(min(eta_a, 6.0), -6.0))
            return lh, la

        def objective(p_mu, p_ha, p_att, p_dfn) -> float:
            ll = 0.0
            for w, r in zip(weights, results):
                lh, la = lambdas(r, p_mu, p_ha, p_att, p_dfn)
                ll += w * (
                    r.home_goals * math.log(lh) - lh
                    + r.away_goals * math.log(la) - la
                )
            ll /= total_weight
            penalty = 0.5 * self.ridge * (
                sum(v * v for v in p_att.values())
                + sum(v * v for v in p_dfn.values())
            )
            return ll - penalty

        def recenter(p_mu, p_ha, p_att, p_dfn):
            ma = sum(p_att.values()) / len(p_att)
            md = sum(p_dfn.values()) / len(p_dfn)
            for t in teams:
                p_att[t] -= ma
                p_dfn[t] -= md
            return p_mu + ma - md, p_ha

        best = objective(mu, home_adv, att, dfn)
        lr = self.lr
        for _ in range(self.iterations):
            g_mu = g_ha = 0.0
            g_att = {t: 0.0 for t in teams}
            g_dfn = {t: 0.0 for t in teams}
            for w, r in zip(weights, results):
                lh, la = lambdas(r, mu, home_adv, att, dfn)
                res_h = r.home_goals - lh
                res_a = r.away_goals - la
                g_mu += w * (res_h + res_a)
                g_ha += w * res_h
                g_att[r.home] += w * res_h
                g_att[r.away] += w * res_a
                g_dfn[r.away] -= w * res_h
                g_dfn[r.home] -= w * res_a
            scale = 1.0 / total_weight
            g_mu *= scale
            g_ha *= scale
            for t in teams:
                g_att[t] = g_att[t] * scale - self.ridge * att[t]
                g_dfn[t] = g_dfn[t] * scale - self.ridge * dfn[t]

            improved = False
            while lr > 1e-7:
                t_att = {t: att[t] + lr * g_att[t] for t in teams}
                t_dfn = {t: dfn[t] + lr * g_dfn[t] for t in teams}
                t_mu = mu + lr * g_mu
                t_ha = home_adv + lr * g_ha
                t_mu, t_ha = recenter(t_mu, t_ha, t_att, t_dfn)
                val = objective(t_mu, t_ha, t_att, t_dfn)
                if val > best + 1e-10:
                    att, dfn, mu, home_adv = t_att, t_dfn, t_mu, t_ha
                    best = val
                    improved = True
                    lr = min(lr * 1.3, 2.0)
                    break
                lr *= 0.5
            if not improved:
                break

        self.mu = mu
        self.home_adv = home_adv
        self.attack = att
        self.defence = dfn
        self.fitted = True
        self.rho = self._fit_rho(results, weights)
        self.kappa = self._fit_kappa(results, weights)
        return self

    def _fit_kappa(self, results: Sequence[MatchResult], weights) -> float:
        """Calibración de un parámetro por log-loss en el propio entrenamiento.

        El Poisson crudo suele ser demasiado tajante (favoritos demasiado
        favoritos). Elevar a ``kappa > 1`` afila, ``kappa < 1`` aplana.
        """
        raw = []
        for r in results:
            m = self.score_matrix(r.home, r.away)
            n = len(m)
            home = sum(m[x][y] for x in range(n) for y in range(n) if x > y)
            draw = sum(m[i][i] for i in range(n))
            away = sum(m[x][y] for x in range(n) for y in range(n) if x < y)
            actual = (
                "home" if r.home_goals > r.away_goals
                else "away" if r.home_goals < r.away_goals
                else "draw"
            )
            raw.append(({"home": home, "draw": draw, "away": away}, actual))

        def loss(k: float) -> float:
            total = 0.0
            for probs, actual in raw:
                adj = {o: p ** k for o, p in probs.items()}
                s = sum(adj.values()) or 1.0
                total += -math.log(max(adj[actual] / s, 1e-9))
            return total / max(len(raw), 1)

        best_k, best_loss = 1.0, loss(1.0)
        k = 0.50
        while k <= 2.001:
            value = loss(k)
            if value < best_loss:
                best_k, best_loss = k, value
            k += 0.05
        lo, hi = max(best_k - 0.05, 0.1), best_k + 0.05
        for _ in range(50):
            m1 = lo + (hi - lo) / 3
            m2 = hi - (hi - lo) / 3
            if loss(m1) > loss(m2):
                lo = m1
            else:
                hi = m2
        return round((lo + hi) / 2, 4)

    def _calibrate(self, probs: dict[str, float]) -> dict[str, float]:
        if self.kappa == 1.0:
            return probs
        adj = {o: max(p, 1e-9) ** self.kappa for o, p in probs.items()}
        s = sum(adj.values())
        return {o: p / s for o, p in adj.items()} if s > 0 else probs

    def _fit_rho(self, results: Sequence[MatchResult], weights) -> float:
        """Ajusta rho con búsqueda en rejilla + refinamiento local."""
        lambdas = [self._lambdas(r.home, r.away) for r in results]

        def loglik(rho: float) -> float:
            total = 0.0
            for (lh, la), w, r in zip(lambdas, weights, results):
                t = self._tau(r.home_goals, r.away_goals, lh, la, rho)
                if t <= 1e-9:
                    continue
                total += w * math.log(t)
            return total

        best_rho, best_val = 0.0, loglik(0.0)
        rho = -0.20
        while rho <= 0.2001:
            val = loglik(rho)
            if val > best_val:
                best_rho, best_val = rho, val
            rho += 0.01
        # Refinamiento fino alrededor del mejor punto.
        lo, hi = best_rho - 0.01, best_rho + 0.01
        for _ in range(60):
            m1 = lo + (hi - lo) / 3
            m2 = hi - (hi - lo) / 3
            if loglik(m1) < loglik(m2):
                lo = m1
            else:
                hi = m2
        rho = (lo + hi) / 2
        return round(max(min(rho, 0.19), -0.19), 4)

    # -------------------------------------------------------------- predict
    def _lambdas(self, home: str, away: str) -> tuple[float, float]:
        if not self.fitted:
            raise RuntimeError("el modelo no está ajustado: llama a fit()")
        a_h = self.attack.get(home, 0.0)
        d_h = self.defence.get(home, 0.0)
        a_a = self.attack.get(away, 0.0)
        d_a = self.defence.get(away, 0.0)
        lam_h = math.exp(self.mu + a_h - d_a + self.home_adv)
        lam_a = math.exp(self.mu + a_a - d_h)
        return lam_h, lam_a

    @staticmethod
    def _tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
        if x == 0 and y == 0:
            return max(1.0 - lh * la * rho, 1e-9)
        if x == 1 and y == 0:
            return max(1.0 + lh * rho, 1e-9)
        if x == 0 and y == 1:
            return max(1.0 + la * rho, 1e-9)
        if x == 1 and y == 1:
            return max(1.0 - rho, 1e-9)
        return 1.0

    def score_matrix(self, home: str, away: str) -> list[list[float]]:
        lh, la = self._lambdas(home, away)
        n = self.max_goals + 1
        matrix = [[0.0] * n for _ in range(n)]
        total = 0.0
        for x in range(n):
            for y in range(n):
                p = _poisson_pmf(lh, x) * _poisson_pmf(la, y)
                p *= self._tau(x, y, lh, la, self.rho)
                matrix[x][y] = p
                total += p
        if total > 0:
            for x in range(n):
                for y in range(n):
                    matrix[x][y] /= total
        return matrix

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        return self._lambdas(home, away)

    def probabilities(
        self, event: Event, quotes: Sequence[Quote] | None = None
    ) -> MarketProbabilities:
        matrix = self.score_matrix(event.home, event.away)
        n = len(matrix)
        probs: MarketProbabilities = {}

        home = sum(matrix[x][y] for x in range(n) for y in range(n) if x > y)
        draw = sum(matrix[i][i] for i in range(n))
        away = sum(matrix[x][y] for x in range(n) for y in range(n) if x < y)
        probs["h2h"] = self._calibrate({"home": home, "draw": draw, "away": away})
        lh, la = self._lambdas(event.home, event.away)
        probs["h2h_ht"] = self._half_time_probs(lh, la)

        # Calculamos exactamente las líneas que ofrecen las casas, más la 2.5
        # como referencia. Así el bot puede analizar cualquier mercado de goles,
        # hándicap o goles por equipo sin hardcodear líneas.
        requested: set[tuple[str, float]] = {("totals", 2.5)}
        if quotes:
            for q in quotes:
                market, point = split_key(q.key)
                if point is not None and market in (
                    "totals", "spreads", "team_totals_home", "team_totals_away"
                ):
                    requested.add((market, point))
        for market, point in sorted(requested):
            probs[market_key(market, point)] = self._lined_prob(matrix, market, point)

        btts = sum(matrix[x][y] for x in range(1, n) for y in range(1, n))
        probs["btts"] = {"yes": btts, "no": 1.0 - btts}
        return probs

    @staticmethod
    def _half_time_probs(lh: float, la: float) -> dict[str, float]:
        """Ganador de la primera parte con goles Poisson a media intensidad."""
        half_h, half_a = lh / 2.0, la / 2.0
        home = draw = away = 0.0
        for x in range(7):
            for y in range(7):
                p = _poisson_pmf(half_h, x) * _poisson_pmf(half_a, y)
                if x > y:
                    home += p
                elif x == y:
                    draw += p
                else:
                    away += p
        total = home + draw + away
        return {"home": home / total, "draw": draw / total, "away": away / total}

    @staticmethod
    def _lined_prob(
        matrix: list[list[float]], market: str, point: float
    ) -> dict[str, float]:
        """Probabilidad de un mercado con línea, a partir de la matriz de goles."""
        n = len(matrix)
        over = 0.0
        if market == "totals":
            for x in range(n):
                for y in range(n):
                    if x + y > point:
                        over += matrix[x][y]
            return {"over": over, "under": 1.0 - over}
        if market == "team_totals_home":
            for x in range(n):
                if x > point:
                    over += sum(matrix[x])
            return {"over": over, "under": 1.0 - over}
        if market == "team_totals_away":
            for y in range(n):
                if y > point:
                    over += sum(matrix[x][y] for x in range(n))
            return {"over": over, "under": 1.0 - over}
        if market == "spreads":
            # ``point`` es el hándicap del local: cubre si x + point > y.
            for x in range(n):
                for y in range(n):
                    if x + point > y:
                        over += matrix[x][y]
            return {"home": over, "away": 1.0 - over}
        return {}

    def top_scores(self, home: str, away: str, n: int = 5):
        matrix = self.score_matrix(home, away)
        scores = [
            (f"{x}-{y}", matrix[x][y])
            for x in range(len(matrix))
            for y in range(len(matrix))
        ]
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:n]

    def summary(self) -> str:
        if not self.fitted:
            return "modelo sin ajustar"
        top_att = sorted(self.attack.items(), key=lambda kv: kv[1], reverse=True)[:5]
        return (
            f"Dixon-Coles | {self.n_matches} partidos | mu={self.mu:.3f} "
            f"ventaja_local={self.home_adv:.3f} rho={self.rho:+.3f}\n"
            + "  ataque: "
            + ", ".join(f"{t} {v:+.2f}" for t, v in top_att)
        )
