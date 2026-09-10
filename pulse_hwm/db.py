from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    method          TEXT NOT NULL DEFAULT 'GET',
    timeout_s       REAL NOT NULL DEFAULT 10.0,
    expected_status INTEGER NOT NULL DEFAULT 200,
    keyword         TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id     INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    status_code INTEGER,
    latency_ms  REAL NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_checks_site_ts ON checks(site_id, ts);

CREATE TABLE IF NOT EXISTS hardware_samples (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hw_metric_ts ON hardware_samples(metric, ts);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    type    TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SITES = [
    ("Google", "https://www.google.com"),
    ("GitHub", "https://github.com"),
    ("Cloudflare", "https://1.1.1.1"),
]


class Database:
    """Thread-safe sqlite wrapper (collectors and UI live on different threads)."""

    _instance: "Database | None" = None

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
        if Database._instance is self:
            Database._instance = None

    @classmethod
    def open(cls, path: Path) -> "Database":
        if cls._instance is None:
            cls._instance = cls(path)
        return cls._instance

    @classmethod
    def current(cls) -> "Database | None":
        return cls._instance

    # -- sites ----------------------------------------------------------
    def add_site(self, name: str, url: str, method: str = "GET",
                 timeout_s: float = 10.0, expected_status: int = 200,
                 keyword: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sites (name, url, method, timeout_s, expected_status, keyword, enabled, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (name, url, method, timeout_s, expected_status, keyword, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def remove_site(self, site_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM checks WHERE site_id = ?", (site_id,))
            self._conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            self._conn.commit()

    def set_site_enabled(self, site_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sites SET enabled = ? WHERE id = ?", (int(enabled), site_id)
            )
            self._conn.commit()

    def get_sites(self, include_disabled: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM sites"
        if not include_disabled:
            q += " WHERE enabled = 1"
        q += " ORDER BY id"
        return self._conn.execute(q).fetchall()

    def seed_default_sites(self) -> None:
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) AS n FROM sites").fetchone()["n"]
            if count == 0:
                for name, url in DEFAULT_SITES:
                    self._conn.execute(
                        "INSERT INTO sites (name, url, enabled, created_at) VALUES (?, ?, 1, ?)",
                        (name, url, time.time()),
                    )
                self._conn.commit()

    # -- checks ---------------------------------------------------------
    def insert_check(self, site_id: int, ts: float, status_code: int | None,
                     latency_ms: float, ok: bool, error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO checks (site_id, ts, status_code, latency_ms, ok, error)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (site_id, ts, status_code, latency_ms, int(ok), error),
            )
            self._conn.commit()

    def checks_since(self, site_id: int, since_ts: float) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM checks WHERE site_id = ? AND ts >= ? ORDER BY ts",
            (site_id, since_ts),
        ).fetchall()

    def latest_check(self, site_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM checks WHERE site_id = ? ORDER BY ts DESC LIMIT 1",
            (site_id,),
        ).fetchone()

    # -- hardware samples -------------------------------------------------
    def insert_hardware_samples(self, samples: list[tuple[float, str, float]]) -> None:
        if not samples:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO hardware_samples (ts, metric, value) VALUES (?, ?, ?)", samples
            )
            self._conn.commit()

    def hardware_series(self, metric: str, since_ts: float) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT ts, value FROM hardware_samples WHERE metric = ? AND ts >= ? ORDER BY ts",
            (metric, since_ts),
        ).fetchall()

    # -- events -----------------------------------------------------------
    def insert_event(self, ts: float, level: str, etype: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, level, type, message) VALUES (?, ?, ?, ?)",
                (ts, level, etype, message),
            )
            self._conn.commit()

    def events_since(self, since_ts: float, limit: int = 500) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM events WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (since_ts, limit),
        ).fetchall()

    # -- settings ----------------------------------------------------------
    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # -- retention -----------------------------------------------------------
    def prune(self, retention_days: float) -> dict[str, int]:
        cutoff = time.time() - retention_days * 86400
        counts: dict[str, int] = {}
        with self._lock:
            for table in ("checks", "hardware_samples", "events"):
                cur = self._conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
                counts[table] = cur.rowcount
            self._conn.commit()
        return counts

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")


def open_default() -> Database:
    from pulse_hwm import config

    config.ensure_dirs()
    return Database.open(config.data_dir() / "pulse.db")
