"""Configuración central. TOML + variables de entorno, sin dependencias.

Prioridad (de menor a mayor):
1. Valores por defecto del código.
2. ``config.toml`` si existe.
3. Variables de entorno (secretos y overrides).
4. ``.env`` en el directorio del proyecto (para no exportar a mano).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore


ALL_SPORTS = ("soccer", "basketball", "tennis", "mma", "table_tennis")

SPORT_LABELS = {
    "soccer": "Fútbol",
    "basketball": "Baloncesto",
    "tennis": "Tenis",
    "mma": "MMA/Boxeo",
    "table_tennis": "Tenis de mesa",
}


@dataclass
class ValueSettings:
    min_edge: float = 0.03
    kelly_fraction: float = 0.25
    max_stake_pct: float = 0.02
    bankroll: float = 1000.0
    min_price: float = 1.30
    max_price: float = 12.00
    max_model_disagreement: float = 0.20


@dataclass
class MovementSettings:
    drop_pct: float = 0.08
    rise_pct: float = 0.10
    window_min: int = 90
    min_points: int = 2


@dataclass
class ErrorSettings:
    mispricing_edge: float = 0.05
    divergence_spread: float = 0.12
    method: str = "shin"


@dataclass
class Config:
    # general
    sports: list[str] = field(default_factory=lambda: list(ALL_SPORTS))
    # Vacío = todas las ligas. Si rellenas, solo se analizan esas.
    leagues: list[str] = field(default_factory=list)
    # Sube la severidad de señales en ligas con riesgo de error alto.
    prioritize_error_prone: bool = True
    provider: str = "sample"          # "sample" | "oddsapi" | "manual"
    db_path: str = "betting.db"
    manual_odds_file: str = "data/odds_manual.json"
    poll_interval: int = 180          # segundos entre sondeos
    alert_cooldown_min: int = 120     # no repetir la misma alerta antes de esto
    quiet: bool = False

    # apis
    odds_api_key: str = ""
    regions: str = "eu"
    markets: str = "h2h,spreads,totals"

    # telegram
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # canales alternativos
    notifier: str = "auto"            # auto | console | telegram | ntfy | discord
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    discord_webhook_url: str = ""
    event_day: str = "days:1"         # days:1..3 | next24h | today | tomorrow | hours:N | all
    error_scan_days: int = 7          # errores graves se buscan hasta 7 días
    notify_day: str = ""              # ventana de notificación ('' = igual que event_day)
    safety_mode: str = "safe"         # safe | balanced | all
    min_safe_score: int = 70          # umbral para modo safe
    focus_actionable: bool = True     # solo notificar apuestas recomendadas
    single_book_only: bool = True     # nunca apuestas en dos casas a la vez
    min_single_book_score: int = 45   # umbral para value de una sola casa
    max_risk_level: str = "MEDIO"     # BAJO | MEDIO | ALTO

    value: ValueSettings = field(default_factory=ValueSettings)
    movement: MovementSettings = field(default_factory=MovementSettings)
    errors: ErrorSettings = field(default_factory=ErrorSettings)

    def live_alerts_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


def _load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _apply_dataclass(obj: Any, values: dict[str, Any]) -> None:
    valid = {f.name for f in fields(obj)}
    for key, value in values.items():
        if key in valid:
            setattr(obj, key, value)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    base = Path(path) if path else Path("config.toml")
    if tomllib is not None and base.exists():
        data = tomllib.loads(base.read_text(encoding="utf-8"))
        general = data.get("general", {})
        for key in (
            "sports", "leagues", "prioritize_error_prone", "provider", "db_path",
            "manual_odds_file", "poll_interval", "alert_cooldown_min", "quiet",
            "regions", "markets", "notifier", "ntfy_topic", "ntfy_server",
            "discord_webhook_url", "event_day", "error_scan_days",
            "notify_day", "safety_mode", "min_safe_score", "focus_actionable",
            "single_book_only", "min_single_book_score", "max_risk_level",
        ):
            if key in general:
                setattr(cfg, key, general[key])
        if "value" in data:
            _apply_dataclass(cfg.value, data["value"])
        if "movement" in data:
            _apply_dataclass(cfg.movement, data["movement"])
        if "errors" in data:
            _apply_dataclass(cfg.errors, data["errors"])

    env = {**_load_env_file(Path(".env")), **os.environ}
    mapping = {
        "PROVIDER": ("provider", str),
        "DB_PATH": ("db_path", str),
        "POLL_INTERVAL": ("poll_interval", int),
        "ALERT_COOLDOWN_MIN": ("alert_cooldown_min", int),
        "ODDS_API_KEY": ("odds_api_key", str),
        "REGIONS": ("regions", str),
        "MANUAL_ODDS_FILE": ("manual_odds_file", str),
        "TELEGRAM_TOKEN": ("telegram_token", str),
        "TELEGRAM_CHAT_ID": ("telegram_chat_id", str),
        "NOTIFIER": ("notifier", str),
        "NTFY_TOPIC": ("ntfy_topic", str),
        "NTFY_SERVER": ("ntfy_server", str),
        "DISCORD_WEBHOOK_URL": ("discord_webhook_url", str),
        "EVENT_DAY": ("event_day", str),
        "ERROR_SCAN_DAYS": ("error_scan_days", int),
        "NOTIFY_DAY": ("notify_day", str),
        "SAFETY_MODE": ("safety_mode", str),
        "MIN_SAFE_SCORE": ("min_safe_score", int),
        "FOCUS_ACTIONABLE": ("focus_actionable", str),
        "SINGLE_BOOK_ONLY": ("single_book_only", str),
        "MIN_SINGLE_BOOK_SCORE": ("min_single_book_score", int),
        "MAX_RISK_LEVEL": ("max_risk_level", str),
        "BANKROLL": ("bankroll", float),
    }
    for env_key, (attr, caster) in mapping.items():
        if env_key in env and env[env_key] != "":
            if attr == "bankroll":
                cfg.value.bankroll = float(env[env_key])
            else:
                setattr(cfg, attr, caster(env[env_key]))

    if "SPORTS" in env and env["SPORTS"].strip():
        cfg.sports = [s.strip() for s in env["SPORTS"].split(",") if s.strip()]
    if "LEAGUES" in env and env["LEAGUES"].strip():
        cfg.leagues = [s.strip() for s in env["LEAGUES"].split(",") if s.strip()]

    # Rango permitido: partidos 1-3 días, búsqueda de errores hasta 7 días.
    try:
        cfg.error_scan_days = min(max(int(cfg.error_scan_days), 1), 7)
    except (TypeError, ValueError):
        cfg.error_scan_days = 7
    try:
        cfg.min_safe_score = min(max(int(cfg.min_safe_score), 0), 100)
    except (TypeError, ValueError):
        cfg.min_safe_score = 70
    if str(cfg.safety_mode).lower() not in {"safe", "balanced", "all"}:
        cfg.safety_mode = "safe"
    cfg.focus_actionable = str(cfg.focus_actionable).strip().lower() not in {
        "0", "false", "no", "off", "n", ""
    }
    cfg.single_book_only = str(cfg.single_book_only).strip().lower() not in {
        "0", "false", "no", "off", "n", ""
    }
    cfg.min_single_book_score = min(max(int(cfg.min_single_book_score), 0), 100)
    cfg.max_risk_level = str(cfg.max_risk_level).strip().upper()
    if cfg.max_risk_level not in {"BAJO", "MEDIO", "ALTO"}:
        cfg.max_risk_level = "MEDIO"
    return cfg


def value_config(cfg: Config):
    from .analysis.value import ValueConfig

    v = cfg.value
    kelly = v.kelly_fraction
    max_pct = v.max_stake_pct
    if str(getattr(cfg, "safety_mode", "safe")).lower() == "safe":
        kelly = min(kelly, 0.10)
        max_pct = min(max_pct, 0.01)
    return ValueConfig(
        min_edge=v.min_edge,
        kelly_fraction=kelly,
        max_stake_pct=max_pct,
        bankroll=v.bankroll,
        min_price=v.min_price,
        max_price=v.max_price,
        max_model_disagreement=v.max_model_disagreement,
    )


def movement_config(cfg: Config):
    from .analysis.movement import MovementConfig

    m = cfg.movement
    return MovementConfig(
        drop_pct=m.drop_pct,
        rise_pct=m.rise_pct,
        window_min=m.window_min,
        min_points=m.min_points,
    )


def error_config(cfg: Config):
    from .analysis.errors import ErrorConfig

    e = cfg.errors
    return ErrorConfig(mispricing_edge=e.mispricing_edge, divergence_spread=e.divergence_spread, method=e.method)
