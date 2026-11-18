"""Explicación breve de los factores del partido / de los jugadores.

Se usa información que el bot realmente tiene (equipos, liga, modelo de liga y
mercado). Si el proveedor no da datos de forma, superficie o lesiones, se dice
explícitamente en lugar de inventarlos.
"""

from __future__ import annotations

from . import leagues
from .types import Signal, split_key

SPORT_NOTES = {
    "tennis": "sin datos de superficie/forma en el proveedor: comprueba lesiones y retiradas",
    "table_tennis": "sin datos de forma reciente: revisa la condición de cada jugador",
    "mma": "revisa pesaje, lesiones y historial de cada peleador",
    "basketball": "revisa lesionados y rotación de última hora",
}


def event_summary(event, profile=None) -> str:
    """Frase corta con los factores conocidos del evento."""
    parts: list[str] = []
    sport = getattr(event, "sport", "")
    home = getattr(event, "home", "")
    away = getattr(event, "away", "")

    if sport in ("tennis", "table_tennis", "mma"):
        parts.append(f"{home} vs {away}")
    else:
        parts.append(f"{home} en casa vs {away} fuera")

    if profile is not None:
        if sport == "soccer":
            parts.append(
                f"{profile.name}: media {profile.goals:g} goles, "
                f"{profile.cards:g} tarjetas, {profile.corners:g} córners; "
                f"riesgo de error {profile.error_risk}"
            )
        else:
            parts.append(f"{profile.name} (riesgo de error {profile.error_risk})")
    elif leagues.find(getattr(event, "league", "")) is None:
        parts.append("liga sin perfil estadístico propio")

    note = SPORT_NOTES.get(sport)
    if note:
        parts.append(note)
    return "; ".join(parts)


def _market_note(signal: Signal) -> str:
    market, point = split_key(signal.market)
    if point is None:
        return ""
    profile = leagues.find(signal.league)
    if profile is None:
        return ""
    if signal.sport == "soccer":
        averages = {
            "totals": (profile.goals, "goles"),
            "team_totals_home": (profile.goals / 2, "goles del local"),
            "team_totals_away": (profile.goals / 2, "goles del visitante"),
            "cards": (profile.cards, "tarjetas"),
            "corners": (profile.corners, "córners"),
        }
        average = averages.get(market)
        if average:
            value, unit = average
            return f"línea {point:g} vs media de liga {value:.1f} {unit}"
    return ""


def describe(signal: Signal) -> str:
    """Factores que se mostrarán en la alerta."""
    explicit = getattr(signal, "factors", "")
    extra = _market_note(signal)
    if extra and "línea" in explicit:
        extra = ""  # el proveedor ya explicó la línea
    bits = [b for b in (explicit, extra) if b]
    return "; ".join(bits) if bits else "sin factores adicionales en el proveedor"
