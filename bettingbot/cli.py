"""Interfaz de línea de comandos: ``python run.py <comando>``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta

from . import backtest, leagues
from .alerts import (
    ConsoleNotifier,
    TelegramNotifier,
    build_notifier_for_config,
)
from .config import ALL_SPORTS, SPORT_LABELS, load_config, value_config
from .models.registry import ModelRegistry
from .pipeline import run_once
from .providers.manual import ManualProvider
from .providers.oddsapi import OddsApiProvider
from .providers.sample import SampleProvider, SampleTruthModel
from .storage import Storage


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _parse_sports(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    sports = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [s for s in sports if s not in ALL_SPORTS]
    if unknown:
        raise SystemExit(
            f"deporte desconocido: {', '.join(unknown)}. "
            f"Válidos: {', '.join(ALL_SPORTS)}"
        )
    return sports


def _build_provider(cfg):
    if cfg.provider == "oddsapi":
        if not cfg.odds_api_key:
            raise SystemExit(
                "Falta ODDS_API_KEY. Ponla en .env o cambia provider=\"sample\" "
                "en config.toml."
            )
        return OddsApiProvider(
            cfg.odds_api_key, cfg.regions, cfg.markets, verbose=not cfg.quiet
        )
    if cfg.provider == "manual":
        return ManualProvider(cfg.manual_odds_file, verbose=not cfg.quiet)
    return SampleProvider()


def _build_registry(cfg, provider) -> ModelRegistry:
    registry = ModelRegistry()
    if isinstance(provider, SampleProvider):
        truth = SampleTruthModel(provider)
        for sport in cfg.sports:
            registry.register(sport, truth)
        return registry

    # Sin histórico no hay modelo de fútbol, pero tarjetas y córners sí pueden
    # analizarse con los priors de liga. Se registran siempre para que el
    # proveedor manual (o la API) tenga contra qué comparar esos mercados.
    if "soccer" in cfg.sports:
        from .models.cards import CardsModel
        from .models.corners import CornersModel
        from .models.football import HalfTimeModel

        registry.register("soccer", CardsModel())
        registry.register("soccer", CornersModel())
        registry.register("soccer", HalfTimeModel())
    return registry


def cmd_demo(args) -> int:
    cfg = load_config()
    cfg.alert_cooldown_min = 0
    sports = _parse_sports(args.sports)
    if sports:
        cfg.sports = sports

    provider = SampleProvider(steps=args.steps)
    registry = _build_registry(cfg, provider)
    storage = Storage(":memory:")
    notifier = ConsoleNotifier()

    print("=" * 68)
    print("DEMO — datos sintéticos, sin red y sin gastar API")
    print("Deportes:", ", ".join(SPORT_LABELS.get(s, s) for s in cfg.sports))
    print(f"Sondeos: {args.steps} · cada {provider.interval_min} min simulados")
    print("=" * 68)

    total_signals = 0
    last = None
    for i in range(args.steps):
        when = (
            provider.base_time + timedelta(minutes=provider.interval_min * i)
        ).isoformat(timespec="minutes")
        print(f"\n--- sondeo {i + 1}/{args.steps} (as_of {when}) ---")
        result = run_once(provider, storage, registry, notifier, cfg)
        last = result
        total_signals += len(result.signals)
        print(
            f"  eventos={result.events} cuotas={result.quotes} "
            f"señales={len(result.signals)} enviadas={result.sent}"
        )

    # Modo "solo cuotas": sin modelo, que es como arranca el bot con la API real
    # hasta que ajustes los modelos. Aquí es donde brillan los errores de casa.
    if last is not None and last.snapshot is not None:
        from .alerts import format_brief
        from .pipeline import analyse

        market_only = analyse(
            storage.get_events(), last.snapshot.quotes, ModelRegistry(), cfg, None
        )
        print("\n--- modo sin modelo (solo cuotas): errores y arbitraje ---")
        print("    (señales informativas en una línea; no son apuestas recomendadas)")
        if not market_only:
            print("  ninguna anomalía")
        for signal in market_only[:6]:
            print("  " + format_brief(signal))

    print("\n" + "=" * 68)
    print("RESUMEN")
    print("=" * 68)
    print(f"Eventos: {storage.event_count()} · cuotas: {storage.quote_count()}")
    print(f"Alertas únicas: {storage.alert_count()} · señales vistas: {total_signals}")
    stats = storage.stats_by_kind()
    if stats:
        print("Por tipo: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    print("\nLectura rápida:")
    print("  VALUE       cuota por encima de la probabilidad del modelo")
    print("  MISPRICING  una casa se desvía del consenso del mercado")
    print("  STEAM/DRIFT la cuota baja o sube rápido en la ventana")
    print("  ARB         arbitraje entre casas (raro y efímero)")
    print("\nSiguiente paso: ejecuta `python run.py poll` con tus claves reales.")
    storage.close()
    return 0


def cmd_poll(args) -> int:
    cfg = load_config()
    sports = _parse_sports(args.sports)
    if sports:
        cfg.sports = sports
    provider = _build_provider(cfg)
    registry = _build_registry(cfg, provider)
    storage = Storage(cfg.db_path)
    notifier = ConsoleNotifier() if args.console else build_notifier_for_config(cfg)

    if not registry.sports():
        print(
            "[aviso] sin modelos ajustados: solo se detectarán movimientos de "
            "cuota y errores de casa, no value bets.\n"
            "        Para value bets necesitas ajustar un modelo (ver README).",
            file=sys.stderr,
        )

    result = run_once(provider, storage, registry, notifier, cfg)
    print(
        f"eventos={result.events} cuotas={result.quotes} "
        f"señales={len(result.signals)} enviadas={result.sent} "
        f"fallos={result.failures}"
    )
    print(f"canal: {notifier.name} · base de datos: {cfg.db_path}")
    storage.close()
    return 0


def cmd_watch(args) -> int:
    cfg = load_config()
    if args.interval:
        cfg.poll_interval = args.interval
    provider = _build_provider(cfg)
    registry = _build_registry(cfg, provider)
    storage = Storage(cfg.db_path)
    notifier = build_notifier_for_config(cfg)

    print(f"Vigilando cada {cfg.poll_interval}s · canal {notifier.name} · Ctrl+C para salir")
    try:
        while True:
            stamp = datetime.now().strftime("%H:%M:%S")
            try:
                result = run_once(provider, storage, registry, notifier, cfg)
                print(
                    f"[{stamp}] eventos={result.events} cuotas={result.quotes} "
                    f"señales={len(result.signals)} enviadas={result.sent}"
                )
            except Exception as exc:  # no dejar de vigilar por un fallo puntual
                print(f"[{stamp}] error: {exc}", file=sys.stderr)
            time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        print("\nparado")
    finally:
        storage.close()
    return 0


def cmd_report(args) -> int:
    cfg = load_config()
    storage = Storage(cfg.db_path)
    stats = storage.stats_by_kind()
    print(f"Base de datos: {cfg.db_path}")
    print(f"Eventos: {storage.event_count()} · cuotas: {storage.quote_count()} · "
          f"alertas: {storage.alert_count()}")
    if stats:
        print("Por tipo: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    rows = storage.recent_alerts(args.limit)
    if not rows:
        print("\nSin alertas todavía. Ejecuta `python run.py poll`.")
    else:
        print(f"\nÚltimas {len(rows)} alertas:")
        for row in rows:
            payload = json.loads(row["payload"])
            print(
                f"  [{row['created_at'][:16]}] {row['kind']:<10} "
                f"{payload.get('match', '')} · {payload.get('outcome', '')} "
                f"@{payload.get('price', 0):.2f} ({payload.get('bookmaker', '')})"
            )
    storage.close()
    return 0


def cmd_test_telegram(args) -> int:
    cfg = load_config()
    if not cfg.live_alerts_enabled():
        print("Faltan TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID en .env", file=sys.stderr)
        return 2
    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    ok = notifier.send(
        "<b>Bot de apuestas conectado</b>\nSi ves esto, las alertas funcionan."
    )
    print("enviado correctamente" if ok else "fallo al enviar")
    return 0 if ok else 1


def cmd_test_notify(args) -> int:
    cfg = load_config()
    notifier = build_notifier_for_config(cfg)
    ok = notifier.send("[TEST] Bot de apuestas: si ves esto, las alertas funcionan.")
    print(f"canal: {notifier.name} · {'enviado correctamente' if ok else 'fallo al enviar'}")
    return 0 if ok else 1


def cmd_preview(args) -> int:
    """Muestra una alerta de ejemplo de cada tipo, para aprender a leerlas."""
    from datetime import datetime, timedelta, timezone

    from .alerts import format_brief, format_signal_plain
    from .safety import assess
    from .types import Signal

    now = datetime.now(timezone.utc)

    def kick(days: int, hours: int = 0, minutes: int = 0):
        return now + timedelta(days=days, hours=hours, minutes=minutes)

    examples = [
        Signal(
            kind="VALUE", sport="soccer", league="Serie B",
            match="Napoli vs Empoli",
            commence=kick(1),
            market="h2h", outcome="home", bookmaker="bet365", price=1.45,
            fair_prob=0.76, edge=0.102, stake=15.00,
            detail="modelo Dixon-Coles 76% vs cuota 1.45 (justa 1.32) · Kelly 10%",
            factors="Napoli en casa vs Empoli fuera; Serie B: media 2.6 goles; riesgo de error medio",
            event_id="demo-single-soccer",
        ),
        Signal(
            kind="VALUE", sport="soccer", league="LaLiga 2",
            match="Betis B vs Sevilla B",
            commence=kick(1),
            market="h2h_ht", outcome="home", bookmaker="bet365", price=1.85,
            fair_prob=0.58, edge=0.07, stake=6.00,
            detail="modelo 1ª parte 58% vs cuota 1.85 · Kelly 10%",
            factors="Betis B en casa vs Sevilla B fuera; media 2.5 goles; 1ª parte más igualada que el resultado final",
            event_id="demo-ht",
        ),
        Signal(
            kind="VALUE", sport="tennis", league="ATP Challenger",
            match="Ruud vs Dimitrov",
            commence=kick(1),
            market="sets_home@0.5", outcome="over", bookmaker="pinnacle", price=1.30,
            fair_prob=0.82, edge=0.066, stake=8.00,
            detail="modelo de sets 82% vs cuota 1.30 · Kelly 10%",
            factors="Ruud vs Dimitrov; gana al menos un set; sin datos de superficie/forma",
            event_id="demo-sets",
        ),
        Signal(
            kind="VALUE", sport="tennis", league="ATP Challenger",
            match="Alcaraz vs Sonego",
            commence=kick(1),
            market="h2h", outcome="home", bookmaker="pinnacle", price=1.35,
            fair_prob=0.78, edge=0.053, stake=12.00,
            detail="modelo Elo 78% vs cuota 1.35 (justa 1.28) · Kelly 10%",
            factors="Alcaraz vs Sonego; sin datos de superficie/forma: comprueba lesiones y retiradas",
            event_id="demo-single-tennis",
        ),
        Signal(
            kind="VALUE", sport="soccer", league="Serie B",
            match="Palermo vs Bari",
            commence=kick(1),
            market="h2h", outcome="home", bookmaker="bet365", price=1.85,
            fair_prob=0.60, edge=0.11, stake=6.00,
            detail="modelo Dixon-Coles 60% vs cuota 1.85 (justa 1.67) · Kelly 10%",
            event_id="demo-value",
            factors="Palermo en casa vs Bari fuera; Serie B: media 2.6 goles; riesgo de error medio",
        ),
        Signal(
            kind="ARB", sport="soccer", league="Serie B",
            match="Palermo vs Bari",
            commence=kick(1),
            market="totals@2.5", outcome="under", bookmaker="betfair", price=2.16,
            fair_prob=None, edge=0.031, stake=None,
            detail="arbitraje +3.1%: over 2.10 en bet365, under 2.16 en betfair",
            severity=3,
            event_id="demo-arb",
            factors="Palermo en casa vs Bari fuera; dos casas con precios opuestos en goles",
        ),
        Signal(
            kind="MISPRICING", sport="soccer", league="Championship",
            match="Leeds vs Norwich",
            commence=kick(1),
            market="h2h", outcome="away", bookmaker="bet365", price=4.50,
            fair_prob=0.27, edge=0.215, stake=12.00,
            detail="la cuota está 18% por encima de la mediana del mercado",
            severity=3,
            event_id="demo-mispricing",
        ),
        Signal(
            kind="STEAM", sport="basketball", league="Liga ACB",
            match="Granada vs Bilbao",
            commence=kick(1),
            market="totals@160.5", outcome="under", bookmaker="pinnacle", price=1.90,
            detail="bajó de 2.05 a 1.90 en 12 min (-7.3%)",
            event_id="demo-steam",
        ),
        Signal(
            kind="DRIFT", sport="tennis", league="ATP Challenger",
            match="Coria vs Burruchaga",
            commence=kick(1),
            market="h2h", outcome="home", bookmaker="betfair", price=2.20,
            detail="subió de 1.95 a 2.20 en 20 min (+12.8%)",
            event_id="demo-drift",
            factors="Coria vs Burruchaga; sin datos de superficie/forma en el proveedor: comprueba lesiones y retiradas",
        ),
        Signal(
            kind="DIVERGENCE", sport="soccer", league="Süper Lig",
            match="Trabzonspor vs Antalyaspor",
            commence=kick(1),
            market="cards@4.5", outcome="over", bookmaker="bet365", price=2.30,
            detail="rango 1.85–2.30 entre casas (24%)",
            event_id="demo-divergence",
        ),
        Signal(
            kind="VALUE", sport="mma", league="UFC Fight Night",
            match="Silva vs Johnson",
            commence=kick(1, hours=7),
            market="h2h", outcome="home", bookmaker="pinnacle", price=2.35,
            fair_prob=0.47, edge=0.104, stake=15.30,
            detail="modelo Elo 47.0% vs cuota 2.35 (justa 2.13) · Kelly 10%",
            event_id="demo-mma",
            factors="Silva vs Johnson; revisa pesaje, lesiones y historial de cada peleador",
        ),
        Signal(
            kind="VALUE", sport="table_tennis", league="TT Star Series",
            match="J. Fernández vs T. Ort",
            commence=kick(0, minutes=30),
            market="totals@11.5", outcome="over", bookmaker="betfair", price=2.02,
            fair_prob=0.53, edge=0.071, stake=11.80,
            detail="modelo Elo 53.0% vs cuota 2.02 (justa 1.89) · Kelly 10%",
            event_id="demo-tt",
            factors="J. Fernández vs T. Ort; sin datos de forma reciente: revisa la condición de cada jugador",
        ),
    ]

    cfg = load_config()
    notifier = build_notifier_for_config(cfg)
    single = getattr(cfg, "single_book_only", True)

    def actionable(sig) -> bool:
        if single and sig.kind == "ARB":
            return False
        return assess(sig).decision == "APOSTAR"

    print("Así se leen las alertas (ejemplos inventados):\n")
    sent = 0
    for sig in examples:
        if sig.kind == "ARB" and single:
            print(
                "[INFO][ARBITRAJE] NO APOSTAR · necesitas dos casas; "
                "este bot solo apuesta en una · sin apuesta recomendada"
            )
        elif actionable(sig):
            print(format_signal_plain(sig))
        else:
            print(format_brief(sig))
        print()
        if args.send and actionable(sig):
            notifier.send(notifier.format(sig))
            sent += 1
    if args.send:
        print(f"enviados {sent} apuestas recomendadas por {notifier.name}")
        print("(el resto se muestra abreviado y no se envía: prioridad a lo que sí apostarías)")
    else:
        print("Añade --send para recibir solo las apuestas recomendadas en tu canal.")
    return 0


def cmd_backtest(args) -> int:
    rows = backtest.load_csv(args.csv, book=args.book)
    print(f"Partidos cargados: {len(rows)} desde {args.csv}")
    with_odds = sum(1 for r in rows if r.odds)
    print(f"Con cuotas comparables: {with_odds}\n")
    report = backtest.evaluate(
        rows,
        min_edge=args.min_edge,
        train_ratio=args.train,
        book=args.book,
        max_price=args.max_price,
    )
    print(report.summary())
    return 0


def cmd_config(args) -> int:
    cfg = load_config()
    print("Deportes:", ", ".join(cfg.sports))
    print("Proveedor:", cfg.provider)
    print("Base de datos:", cfg.db_path)
    print("Intervalo:", f"{cfg.poll_interval}s")
    print("Partidos:", cfg.event_day, "(rango 1-3 días)")
    print("Notificaciones:", cfg.notify_day or cfg.event_day)
    print("Errores graves:", f"hasta {cfg.error_scan_days} días")
    print("Seguridad:", cfg.safety_mode, f"(umbral {cfg.min_safe_score})")
    print("Enfoque:", "solo apuestas recomendadas" if cfg.focus_actionable else "todas las señales")
    print("Casas:", "una sola casa" if cfg.single_book_only else "arbitrajes permitidos")
    print("Riesgo máximo:", cfg.max_risk_level)
    print(
        "Ligas:",
        ", ".join(leagues.filter_keys(cfg.leagues)) if cfg.leagues else "todas",
        f"· priorizar errores={cfg.prioritize_error_prone}",
    )
    effective = value_config(cfg)
    print("Value:", f"edge>={cfg.value.min_edge:.0%}",
          f"kelly={effective.kelly_fraction:.0%}",
          f"banca={cfg.value.bankroll:.0f}",
          f"tope={effective.max_stake_pct:.0%}")
    print("Movimiento:", f"baja>={cfg.movement.drop_pct:.0%}",
          f"sube>={cfg.movement.rise_pct:.0%}",
          f"ventana={cfg.movement.window_min}min")
    print("Errores:", f"desvío>={cfg.errors.mispricing_edge:.0%}",
          f"método={cfg.errors.method}")
    print("Telegram:", "configurado" if cfg.live_alerts_enabled() else "no configurado")
    print("Canal de alertas:", build_notifier_for_config(cfg).name)
    print("API key odds:", "configurada" if cfg.odds_api_key else "no configurada")
    return 0


def cmd_leagues(args) -> int:
    sport = args.sport or None
    if sport and sport not in ALL_SPORTS:
        raise SystemExit(f"deporte desconocido: {sport}")
    print(leagues.describe(sport, max_tier=args.max_tier))
    print(
        "\nPrioridad = riesgo de error + liga secundaria. Las de arriba son las "
        "que más merece la pena vigilar."
    )
    return 0


MARKETS_HELP = [
    ("h2h", "Ganador (1X2 / moneyline)", "todos los proveedores"),
    ("totals", "Goles/puntos over-under en cualquier línea", "Odds API, sample, manual"),
    ("btts", "Ambos equipos marcan", "sample, manual (Odds API según liga)"),
    ("team_totals_home/away", "Goles de un solo equipo", "sample, manual (Odds API a veces)"),
    ("spreads", "Hándicap asiático/europeo", "Odds API, sample, manual"),
    ("cards", "Tarjetas over-under", "manual (la API casi no lo cubre)"),
    ("corners", "Córners over-under", "manual (la API casi no lo cubre)"),
    ("sets_home / sets_away", "Gana al menos 1 set (tenis)", "manual, sample; Odds API según liga"),
    ("h2h_ht", "Ganador de la 1ª parte (local/empate/visitante)", "manual, sample; Odds API h2h_1h"),
]


def cmd_markets(args) -> int:
    print(f"{'MERCADO':26s} {'QUÉ ES':44s} COBERTURA")
    for key, desc, coverage in MARKETS_HELP:
        print(f"{key:26s} {desc:44s} {coverage}")
    print(
        "\nNota: para tarjetas y córners usa provider=\"manual\" y escribe las "
        "cuotas en data/odds_manual.json.\n"
        "El modelo de fútbol calcula la línea exacta que ofrezca la casa, "
        "no solo 2.5."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bettingbot",
        description="Bot de análisis de apuestas: value, movimiento de cuotas y errores de casa.",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("demo", help="pipeline completo con datos sintéticos (sin red)")
    p.add_argument("--steps", type=int, default=4, help="número de sondeos simulados")
    p.add_argument("--sports", default="", help="lista separada por comas")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("poll", help="un sondeo real y análisis")
    p.add_argument("--sports", default="", help="lista separada por comas")
    p.add_argument("--console", action="store_true", help="imprimir en consola en vez de Telegram")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("watch", help="bucle continuo de sondeo y alertas")
    p.add_argument("--interval", type=int, default=0, help="segundos entre sondeos")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("report", help="ver alertas recientes")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("test-telegram", help="enviar un mensaje de prueba")
    p.set_defaults(func=cmd_test_telegram)

    p = sub.add_parser("test-notify", help="probar el canal de alertas configurado")
    p.set_defaults(func=cmd_test_notify)

    p = sub.add_parser("backtest", help="evaluar el modelo de fútbol contra el mercado")
    p.add_argument("--csv", required=True, help="CSV de football-data.co.uk")
    p.add_argument("--min-edge", type=float, default=0.03)
    p.add_argument("--train", type=float, default=0.7, help="proporción de entrenamiento")
    p.add_argument(
        "--book",
        default="b365",
        choices=sorted(backtest.BOOKS),
        help="casa de referencia (b365 = casa blanda, pinnacle = cierre sharp)",
    )
    p.add_argument(
        "--max-price",
        type=float,
        default=None,
        help="ignorar cuotas por encima de este valor (evita longshots)",
    )
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("config", help="mostrar configuración efectiva")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("leagues", help="catálogo de ligas con prioridad y priors")
    p.add_argument("--sport", default="", help="soccer, basketball, tennis, mma, table_tennis")
    p.add_argument("--max-tier", type=int, default=3, help="1=élite, 3=nicho")
    p.set_defaults(func=cmd_leagues)

    p = sub.add_parser("preview", help="ver una alerta de ejemplo de cada tipo, explicada")
    p.add_argument("--send", action="store_true", help="enviarlas también al canal configurado")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("markets", help="mercados soportados y qué proveedor los da")
    p.set_defaults(func=cmd_markets)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)
