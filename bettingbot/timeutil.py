"""Utilidades de fecha para alertas y filtro de eventos por día.

Toda la hora se muestra en hora de España. No usamos ``zoneinfo`` porque en
Windows necesitaría el paquete ``tzdata``; aplicamos directamente las reglas de
la UE (CET +1 / CEST +2, último domingo de marzo y de octubre a las 01:00 UTC).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

DAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MONTHS = (
    "", "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
)


def last_sunday(year: int, month: int) -> datetime:
    """Último domingo del mes indicado."""
    if month == 12:
        last = datetime(year, 12, 31)
    else:
        last = datetime(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def to_spain(commence) -> datetime | None:
    """Convierte un datetime (aware o naive en UTC) a hora de España naive."""
    if not isinstance(commence, datetime):
        return None
    if commence.tzinfo is None:
        utc = commence.replace(tzinfo=timezone.utc)
    else:
        utc = commence.astimezone(timezone.utc)
    start = last_sunday(utc.year, 3).replace(hour=1, tzinfo=timezone.utc)
    end = last_sunday(utc.year, 10).replace(hour=1, tzinfo=timezone.utc)
    offset = 2 if start <= utc < end else 1
    return (utc + timedelta(hours=offset)).replace(tzinfo=None)


def spain_today() -> date:
    return to_spain(datetime.now(timezone.utc)).date()


def format_kickoff(commence) -> str:
    """Fecha y hora legible, por ejemplo ``mié 2 dic 21:00 (hora de España)``."""
    dt = to_spain(commence)
    if dt is None:
        return "fecha por confirmar"
    return (
        f"{DAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month]} "
        f"{dt.hour:02d}:{dt.minute:02d} (hora de España)"
    )


def in_days(commence, days: float, now=None) -> bool:
    """¿El evento está entre ahora y ``days`` días en el futuro?"""
    if commence is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (
        commence if commence.tzinfo is not None
        else commence.replace(tzinfo=timezone.utc)
    ) - now
    return timedelta(0) <= delta <= timedelta(days=float(days))


def in_day(commence, mode: str = "next24h", now=None) -> bool:
    """¿Está el evento dentro de la ventana elegida?

    Modos:
      * ``days:1`` / ``days:2`` / ``days:3``: próximos 1 a 3 días.
      * ``next24h`` (por defecto): próximas 24 horas, un día móvil.
      * ``today`` / ``tomorrow``: día natural en hora de España.
      * ``hours:N``: próximas N horas.
      * ``all``: sin filtro.
    """
    mode = (mode or "next24h").lower()
    if mode == "all":
        return True
    if commence is None:
        return False
    if mode.startswith("days:"):
        try:
            days = float(mode.split(":", 1)[1])
        except ValueError:
            days = 1.0
        return in_days(commence, min(max(days, 1.0), 3.0), now)
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if commence.tzinfo is not None:
        delta = commence - now
    else:
        delta = commence.replace(tzinfo=timezone.utc) - now

    if mode == "next24h":
        return timedelta(0) <= delta <= timedelta(hours=24)
    if mode.startswith("hours:"):
        try:
            hours = float(mode.split(":", 1)[1])
        except ValueError:
            hours = 24.0
        return timedelta(0) <= delta <= timedelta(hours=hours)

    local = to_spain(commence)
    if local is None:
        return False
    if mode == "today":
        return local.date() == spain_today()
    if mode == "tomorrow":
        return local.date() == spain_today() + timedelta(days=1)
    # Valor desconocido: no filtramos para no perder alertas.
    return True
