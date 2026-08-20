from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np

from src.portfolio.position import Position
from src.strategy.base import Signal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    is_open      INTEGER NOT NULL,
    open_signal  TEXT,
    close_signal TEXT,
    open_order   TEXT,
    close_order  TEXT,
    tp_order     TEXT,
    sl_order     TEXT,
    open_fills   TEXT,
    close_fills  TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_positions_is_open ON positions(is_open);
"""

_JSON_COLUMNS = (
    "open_signal",
    "close_signal",
    "open_order",
    "close_order",
    "tp_order",
    "sl_order",
    "open_fills",
    "close_fills",
)


def _json_default(obj):
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)


def _to_json(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=_json_default)


def _from_json(raw: str | None):
    if raw is None:
        return None
    return json.loads(raw)


def _to_signal(raw: str | None) -> Signal | None:
    if raw is None:
        return None
    return Signal(**json.loads(raw))


class PositionStore:
    def __init__(self, db_path: Path = Path("data/positions.db")):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, position: Position) -> int:
        values = {
            "symbol": position.symbol,
            "side": position.side,
            "is_open": int(position.is_open),
            **{col: _to_json(getattr(position, col)) for col in _JSON_COLUMNS},
        }
        with self._conn:
            position_id = position.id
            if position_id is None:
                columns = ", ".join(values)
                placeholders = ", ".join(f":{col}" for col in values)
                cur = self._conn.execute(
                    f"INSERT INTO positions ({columns}) VALUES ({placeholders})", values
                )
                position_id = cur.lastrowid
            else:
                assignments = ", ".join(f"{col} = :{col}" for col in values)
                self._conn.execute(
                    f"UPDATE positions SET {assignments}, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = :id",
                    {**values, "id": position_id},
                )
        position.id = position_id
        return position_id

    def get(self, position_id: int) -> Position | None:
        row = self._conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        return self._row_to_position(row) if row else None

    def get_open(self) -> list[Position]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE is_open = 1 ORDER BY id"
        ).fetchall()
        return [self._row_to_position(row) for row in rows]

    def get_all(self, limit: int = 100) -> list[Position]:
        rows = self._conn.execute(
            "SELECT * FROM positions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_position(row) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            id=row["id"],
            symbol=row["symbol"],
            side=row["side"],
            is_open=bool(row["is_open"]),
            open_signal=_to_signal(row["open_signal"]),
            close_signal=_to_signal(row["close_signal"]),
            open_order=_from_json(row["open_order"]),
            close_order=_from_json(row["close_order"]),
            tp_order=_from_json(row["tp_order"]),
            sl_order=_from_json(row["sl_order"]),
            open_fills=_from_json(row["open_fills"]) or [],
            close_fills=_from_json(row["close_fills"]) or [],
        )
