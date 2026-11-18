"""Valoración de riesgo y recomendación clara: APOSTAR / ESPERAR / NO APOSTAR.

No promete aciertos: puntúa cuánto se puede confiar en cada señal según la
probabilidad del modelo, el tipo de mercado, el deporte y la liga. El objetivo
es quedarse solo con las apuestas más seguras cuando el perfil es ``safe``.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import leagues
from .types import Signal

ERROR_KINDS = {"ARB", "MISPRICING", "DIVERGENCE", "STEAM", "DRIFT"}

SPORT_PENALTY = {
    "tennis": 12,       # retiradas, partidos que se alargan días
    "mma": 15,          # pesaje, lesiones, no contest
    "table_tennis": 10,
    "basketball": 5,
    "soccer": 0,
}

VOLATILE_MARKETS = ("cards", "corners", "totals", "team_totals")


@dataclass(frozen=True)
class Safety:
    score: int                 # 0-100, mayor = más fiable
    level: str                 # BAJO | MEDIO | ALTO
    decision: str              # APOSTAR | ESPERAR | NO APOSTAR
    reasons: tuple[str, ...]

    @property
    def is_safe(self) -> bool:
        return self.level == "BAJO" and self.decision == "APOSTAR"


def assess(signal: Signal) -> Safety:
    reasons: list[str] = []
    kind = signal.kind
    prob = signal.fair_prob or 0.0
    price = signal.price
    market = signal.market.split("@", 1)[0]

    if "ERROR GRAVE" in (signal.detail or "") or signal.severity >= 3 and kind in ERROR_KINDS:
        if kind == "ARB":
            score = 92
            reasons.append("arbitraje: se cubren todos los resultados")
        else:
            score = 28
            reasons.append("error grave: la casa puede estar equivocada o tener información")
    elif kind == "ARB":
        score = 88
        reasons.append("arbitraje: se cubren todos los resultados")
    elif kind == "VALUE" and prob > 0:
        if prob >= 0.75:
            score = 82
        elif prob >= 0.65:
            score = 72
        elif prob >= 0.55:
            score = 58
        else:
            score = 38
        reasons.append(f"probabilidad del modelo {prob:.0%}")

        if price <= 1.60:
            score += 5
            reasons.append("cuota baja: resultado probable")
        elif price >= 3.50:
            score -= 12
            reasons.append("cuota alta: resultado poco probable")

        if market in VOLATILE_MARKETS:
            score -= 10
            reasons.append("mercado de recuento/total: más variable")
    else:
        score = 40 if kind in ("MISPRICING", "DIVERGENCE") else 25
        reasons.append("señal informativa, no una apuesta en sí")

    penalty = SPORT_PENALTY.get(signal.sport, 5)
    if penalty:
        score -= penalty
        reasons.append(f"riesgo propio del deporte ({signal.sport})")

    profile = leagues.find(signal.league)
    if profile is not None and profile.priority >= 4:
        score -= 5
        reasons.append(f"liga con historial de errores ({profile.name})")

    score = max(0, min(100, score))
    level = "BAJO" if score >= 70 else ("MEDIO" if score >= 45 else "ALTO")
    if kind == "ARB":
        decision = "APOSTAR"
    elif level == "BAJO" and signal.edge is not None and signal.edge > 0:
        decision = "APOSTAR"
    elif level == "MEDIO":
        decision = "ESPERAR"
    else:
        decision = "NO APOSTAR"
    if "error grave" in " ".join(reasons) and kind != "ARB":
        decision = "NO APOSTAR"

    # Value con ventaja clara en una sola casa: se recomienda con stake reducido
    # aunque la probabilidad no sea altísima (perfil de una sola casa).
    if (
        kind == "VALUE"
        and signal.edge is not None
        and signal.edge >= 0.05
        and price <= 2.5
        and score >= 45
        and decision != "APOSTAR"
    ):
        level = "BAJO" if score >= 70 else "MEDIO"
        decision = "APOSTAR"
        reasons.append(
            "ventaja moderada en una sola casa · stake reducido" if score < 70
            else "ventaja clara en una sola casa"
        )

    # Error de casa convertido en apuesta simple: se permite con ventaja clara
    # aunque la probabilidad no sea altísima, siempre en una sola casa.
    if (
        getattr(signal, "single_book", False)
        and kind == "VALUE"
        and signal.edge is not None
        and signal.edge >= 0.05
        and score >= 45
    ):
        level = "BAJO" if score >= 70 else "MEDIO"
        decision = "APOSTAR"
        reasons.append("valor estadístico detectado en una sola casa")

    return Safety(score, level, decision, tuple(reasons))
