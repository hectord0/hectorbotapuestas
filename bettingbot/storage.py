"""Persistencia en SQLite: eventos, histórico de cuotas y alertas.

SQLite es suficiente para uso personal: cero configuración, un archivo, y
aguanta millones de snapshots sin despeinarse. El histórico de cuotas es el
activo más valioso del bot: te permite detectar movimientos y, más adelante,
hacer backtesting con tus propias series.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .analysis.movement import PricePoint
from .types import Event, Quote, Signal, market_key, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    sport       TEXT NOT NULL,
    league      TEXT NOT NULL DEFAULT '',
    home        TEXT NOT NULL,
    away        TEXT NOT NULL,
    commence    TEXT NOT NULL,
    extra       TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    bookmaker   TEXT NOT NULL,
    market      TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    point       REAL,
    price       REAL NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quotes_lookup
    ON quotes(event_id, market, outcome, bookmaker, captured_at);
CREATE INDEX IF NOT EXISTS idx_quotes_time ON quotes(captured_at);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    event_id    TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL,
    channel     TEXT NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_dedupe ON alerts(dedupe_key, created_at);
"""


def _iso(dt: datetime) -> str:
    """Siempre en UTC: evita que un cambio de horario rompa el orden del histórico."""
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Storage:
    def __init__(self, path: str | Path = "betting.db") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- eventos
    def upsert_event(self, event: Event) -> None:
        self.conn.execute(
            """
            INSERT INTO events (id, sport, league, home, away, commence, extra, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                sport=excluded.sport, league=excluded.league,
                home=excluded.home, away=excluded.away,
                commence=excluded.commence, extra=excluded.extra
            """,
            (
                event.id, event.sport, event.league, event.home, event.away,
                _iso(event.commence), json.dumps(event.extra or {}), _iso(utcnow()),
            ),
        )

    def upsert_events(self, events: Iterable[Event]) -> int:
        n = 0
        for e in events:
            self.upsert_event(e)
            n += 1
        self.conn.commit()
        return n

    def get_events(self) -> dict[str, Event]:
        rows = self.conn.execute("SELECT * FROM events").fetchall()
        out: dict[str, Event] = {}
        for r in rows:
            out[r["id"]] = Event(
                id=r["id"], sport=r["sport"], league=r["league"],
                home=r["home"], away=r["away"],
                commence=_parse(r["commence"]),
                extra=json.loads(r["extra"] or "{}"),
            )
        return out

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # -------------------------------------------------------------- cuotas
    def add_quotes(self, quotes: Sequence[Quote]) -> int:
        self.conn.executemany(
            """
            INSERT INTO quotes (event_id, bookmaker, market, outcome, point, price, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (q.event_id, q.bookmaker, q.market, q.outcome, q.point, q.price, _iso(q.captured_at))
                for q in quotes
            ],
        )
        self.conn.commit()
        return len(quotes)

    def quote_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]

    def latest_quotes(self, since: datetime | None = None) -> list[Quote]:
        since = since or (utcnow() - timedelta(days=1))
        rows = self.conn.execute(
            "SELECT * FROM quotes WHERE captured_at >= ? ORDER BY captured_at",
            (_iso(since),),
        ).fetchall()
        return [
            Quote(
                event_id=r["event_id"], bookmaker=r["bookmaker"], market=r["market"],
                outcome=r["outcome"], price=r["price"], point=r["point"],
                captured_at=_parse(r["captured_at"]),
            )
            for r in rows
        ]

    def price_history(
        self, since: datetime | None = None
    ) -> dict[tuple, list[PricePoint]]:
        since = since or (utcnow() - timedelta(days=7))
        rows = self.conn.execute(
            """
            SELECT event_id, market, point, outcome, bookmaker, price, captured_at
            FROM quotes WHERE captured_at >= ?
            ORDER BY captured_at
            """,
            (_iso(since),),
        ).fetchall()
        series: dict[tuple, list[PricePoint]] = {}
        for r in rows:
            key = (r["event_id"], market_key(r["market"], r["point"]), r["outcome"], r["bookmaker"])
            series.setdefault(key, []).append(
                PricePoint(t=_parse(r["captured_at"]), price=r["price"])
            )
        return series

    # -------------------------------------------------------------- alertas
    def should_alert(self, dedupe_key: str, cooldown_min: int) -> bool:
        if cooldown_min <= 0:
            return True
        cutoff = utcnow() - timedelta(minutes=cooldown_min)
        row = self.conn.execute(
            "SELECT created_at FROM alerts WHERE dedupe_key = ? ORDER BY created_at DESC LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        if row is None:
            return True
        return _parse(row["created_at"]) < cutoff

    def log_alert(self, signal: Signal, channel: str, delivered: bool) -> None:
        self.conn.execute(
            """
            INSERT INTO alerts (dedupe_key, kind, event_id, payload, channel, delivered, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.dedupe_key, signal.kind, signal.event_id,
                json.dumps(
                    {
                        "match": signal.match,
                        "market": signal.market,
                        "outcome": signal.outcome,
                        "bookmaker": signal.bookmaker,
                        "price": signal.price,
                        "edge": signal.edge,
                        "stake": signal.stake,
                        "detail": signal.detail,
                    },
                    ensure_ascii=False,
                ),
                channel, 1 if delivered else 0, _iso(utcnow()),
            ),
        )
        self.conn.commit()

    def alert_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]

    def recent_alerts(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def stats_by_kind(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) AS n FROM alerts GROUP BY kind ORDER BY n DESC"
        ).fetchall()
        return {r["kind"]: r["n"] for r in rows}
