"""SQLite-backed state shared by the running agent and the creator CLI."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,          -- fund | earn | compute | transfer_out | settle
    amount_usd REAL NOT NULL,    -- signed: credits positive, debits negative
    memo TEXT NOT NULL DEFAULT '',
    settled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    description TEXT NOT NULL,
    bounty_usd REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',  -- open | claimed | submitted | paid | rejected
    result TEXT,
    feedback TEXT
);
CREATE TABLE IF NOT EXISTS control (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # events --------------------------------------------------------------
    def log(self, kind: str, **data: Any) -> None:
        self.db.execute(
            "INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)",
            (time.time(), kind, json.dumps(data, default=str)),
        )

    def events(self, limit: int = 50, after_id: int = 0) -> list[sqlite3.Row]:
        rows = self.db.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?", (after_id, limit)
        ).fetchall()
        return list(reversed(rows))

    # ledger --------------------------------------------------------------
    def record(self, kind: str, amount_usd: float, memo: str = "", settled: bool = True) -> None:
        self.db.execute(
            "INSERT INTO ledger (ts, kind, amount_usd, memo, settled) VALUES (?, ?, ?, ?, ?)",
            (time.time(), kind, amount_usd, memo, int(settled)),
        )

    def ledger_sum(self, *, unsettled_only: bool = False) -> float:
        q = "SELECT COALESCE(SUM(amount_usd), 0) FROM ledger"
        if unsettled_only:
            q += " WHERE settled = 0"
        return float(self.db.execute(q).fetchone()[0])

    def mark_settled(self) -> float:
        owed = -self.ledger_sum(unsettled_only=True)
        self.db.execute("UPDATE ledger SET settled = 1 WHERE settled = 0")
        return owed

    def spent_since(self, since_ts: float, kinds: tuple[str, ...] = ("transfer_out",)) -> float:
        marks = ",".join("?" for _ in kinds)
        row = self.db.execute(
            f"SELECT COALESCE(SUM(-amount_usd), 0) FROM ledger WHERE ts >= ? AND kind IN ({marks})",
            (since_ts, *kinds),
        ).fetchone()
        return float(row[0])

    def ledger_rows(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # notes ---------------------------------------------------------------
    def add_note(self, text: str) -> None:
        self.db.execute("INSERT INTO notes (ts, text) VALUES (?, ?)", (time.time(), text))

    def recent_notes(self, limit: int = 15) -> list[str]:
        rows = self.db.execute("SELECT text FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in reversed(rows)]

    # tasks ---------------------------------------------------------------
    def add_task(self, description: str, bounty_usd: float) -> int:
        cur = self.db.execute(
            "INSERT INTO tasks (ts, description, bounty_usd) VALUES (?, ?, ?)",
            (time.time(), description, bounty_usd),
        )
        return int(cur.lastrowid)

    def task(self, task_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def tasks(self, statuses: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        if not statuses:
            return self.db.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        marks = ",".join("?" for _ in statuses)
        return self.db.execute(
            f"SELECT * FROM tasks WHERE status IN ({marks}) ORDER BY id", statuses
        ).fetchall()

    def update_task(self, task_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))

    # control -------------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM control WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO control (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
