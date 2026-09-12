from __future__ import annotations

import sqlite3
import threading
import time
import uuid as _uuid
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

# ── forward-only migrations ────────────────────────────────────────────
# Each entry runs exactly once (tracked in schema_meta). The cloud-sync
# feature needs stable per-row identity + timestamps: sqlite 1-based
# rowids are recycled/gapped per-device, so sites get a random uuid.
_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        ALTER TABLE sites ADD COLUMN uuid TEXT;
        ALTER TABLE sites ADD COLUMN updated_at REAL NOT NULL DEFAULT 0;
        ALTER TABLE sites ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0;
        UPDATE sites SET updated_at = created_at;
        UPDATE sites SET uuid = lower(hex(randomblob(16)))
            WHERE uuid IS NULL OR uuid = '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sites_uuid ON sites(uuid);

        CREATE TABLE IF NOT EXISTS settings_sync (
            key        TEXT PRIMARY KEY,
            updated_at REAL NOT NULL DEFAULT 0
        );
        INSERT INTO settings_sync (key, updated_at)
            SELECT key, strftime('%s','now') FROM settings;
        """,
    ),
]


def _latest_schema_version() -> int:
    return max((v for v, _ in _MIGRATIONS), default=0)


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
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        """Run each pending _MIGRATIONS entry once (idempotent, locked)."""
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)"
            )
            row = self._conn.execute(
                "SELECT MAX(version) AS v FROM schema_meta"
            ).fetchone()
            current = int(row["v"]) if row and row["v"] is not None else 0
            for version, sql in _MIGRATIONS:
                if version <= current:
                    continue
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_meta (version) VALUES (?)", (version,)
                )
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
    def add_site(
        self,
        name: str,
        url: str,
        method: str = "GET",
        timeout_s: float = 10.0,
        expected_status: int = 200,
        keyword: str = "",
    ) -> int:
        # uuid is the sync identity: stable across devices (local rowids are not)
        site_uuid = _uuid.uuid4().hex  # 32 chars, same format as the backfill
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sites (name, url, method, timeout_s, expected_status, keyword, enabled, created_at, uuid, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    name,
                    url,
                    method,
                    timeout_s,
                    expected_status,
                    keyword,
                    now,
                    site_uuid,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def remove_site(self, site_id: int) -> None:
        # tombstone (deleted=1), not DELETE: the sync engine must be able to
        # tell the other devices "this was removed on purpose"
        self._set_site_bool(site_id, "deleted", True)
        self._set_site_bool(site_id, "enabled", False)

    def _set_site_bool(self, site_id: int, column: str, value: bool) -> None:
        if column not in ("deleted", "enabled"):
            raise ValueError(f"not a toggle column: {column}")
        with self._lock:
            self._conn.execute(
                f"UPDATE sites SET {column} = ?, updated_at = ? WHERE id = ?",
                (int(value), time.time(), site_id),
            )
            self._conn.commit()

    def set_site_enabled(self, site_id: int, enabled: bool) -> None:
        self._set_site_bool(site_id, "enabled", enabled)

    def get_sites(self, include_disabled: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM sites WHERE deleted = 0"
        if not include_disabled:
            q += " AND enabled = 1"
        q += " ORDER BY id"
        return self._conn.execute(q).fetchall()

    def seed_default_sites(self) -> None:
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) AS n FROM sites").fetchone()[
                "n"
            ]
            if count == 0:
                for name, url in DEFAULT_SITES:
                    # uuid + updated_at make seeded sites syncable from
                    # day one (plan_sites keys everything by uuid)
                    now = time.time()
                    self._conn.execute(
                        "INSERT INTO sites (name, url, enabled, created_at, uuid, updated_at)"
                        " VALUES (?, ?, 1, ?, ?, ?)",
                        (name, url, now, _uuid.uuid4().hex, now),
                    )
                self._conn.commit()

    # -- checks ---------------------------------------------------------
    def insert_check(
        self,
        site_id: int,
        ts: float,
        status_code: int | None,
        latency_ms: float,
        ok: bool,
        error: str = "",
    ) -> None:
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
                "INSERT INTO hardware_samples (ts, metric, value) VALUES (?, ?, ?)",
                samples,
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
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            # mirror the timestamp: the sync engine compares per-key LWW
            self._conn.execute(
                "INSERT INTO settings_sync (key, updated_at) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET updated_at = excluded.updated_at",
                (key, now),
            )
            self._conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    # -- sync helpers (settings + sites, per-key/per-row LWW) ───────────

    def get_settings_with_ts(self) -> dict[str, tuple[str, float]]:
        """Every settings key → (value, sync timestamp from settings_sync)."""
        rows = self._conn.execute(
            "SELECT s.key, s.value, COALESCE(sc.updated_at, 0) AS updated_at"
            " FROM settings s LEFT JOIN settings_sync sc ON s.key = sc.key"
        ).fetchall()
        return {r["key"]: (r["value"], float(r["updated_at"])) for r in rows}

    def adopt_cloud_setting(self, key: str, value: str, ts: float) -> bool:
        """Take a cloud write when it is NEWER than the local write.
        Unlike set_setting, the cloud timestamp is preserved as-is."""
        row = self._conn.execute(
            "SELECT updated_at FROM settings_sync WHERE key = ?", (key,)
        ).fetchone()
        if row is not None and float(row["updated_at"]) >= ts:
            return False  # local write wins (LWW)
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.execute(
                "INSERT INTO settings_sync (key, updated_at) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET updated_at = excluded.updated_at",
                (key, ts),
            )
            self._conn.commit()
        return True

    def get_sites_for_sync(self) -> list[sqlite3.Row]:
        """ALL site rows incl. tombstones — the sync engine needs history."""
        return self._conn.execute("SELECT * FROM sites ORDER BY id").fetchall()

    def upsert_synced_site(
        self,
        site_uuid: str,
        name: str,
        url: str,
        method: str = "GET",
        timeout_s: float = 10.0,
        expected_status: int = 200,
        keyword: str = "",
        enabled: bool = True,
        deleted: bool = False,
        updated_at: float = 0.0,
    ) -> bool:
        """Upsert a cloud row by uuid; only applied when NEWER locally.
        Returns True when the local state changed."""
        now = time.time()
        existing = self._conn.execute(
            "SELECT id, updated_at FROM sites WHERE uuid = ?", (site_uuid,)
        ).fetchone()
        if existing is not None and float(existing["updated_at"]) >= updated_at:
            return False
        with self._lock:
            if existing is None:
                # deleted column left at its 0 default; tombstoned rows are
                # patched right below so the stored row reflects cloud truth
                self._conn.execute(
                    "INSERT INTO sites (name, url, method, timeout_s, expected_status, keyword, enabled, created_at, uuid, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        url,
                        method,
                        float(timeout_s),
                        int(expected_status),
                        keyword,
                        int(enabled),
                        now,
                        site_uuid,
                        float(updated_at),
                    ),
                )
                if deleted:
                    # cloud tombstone for a row we never had: record it anyway
                    self._conn.execute(
                        "UPDATE sites SET deleted = 1 WHERE uuid = ?", (site_uuid,)
                    )
            else:
                self._conn.execute(
                    "UPDATE sites SET name=?, url=?, method=?, timeout_s=?, expected_status=?, keyword=?, enabled=?, updated_at=?, deleted=?"
                    " WHERE uuid = ?",
                    (
                        name,
                        url,
                        method,
                        float(timeout_s),
                        int(expected_status),
                        keyword,
                        int(enabled),
                        float(updated_at),
                        int(deleted),
                        site_uuid,
                    ),
                )
            self._conn.commit()
        return True

    def tombstone_site_by_uuid(self, site_uuid: str, ts: float) -> bool:
        existing = self._conn.execute(
            "SELECT updated_at FROM sites WHERE uuid = ?", (site_uuid,)
        ).fetchone()
        if existing is None or float(existing["updated_at"]) >= ts:
            return False
        with self._lock:
            self._conn.execute(
                "UPDATE sites SET deleted = 1, enabled = 0, updated_at = ?"
                " WHERE uuid = ?",
                (ts, site_uuid),
            )
            self._conn.commit()
        return True

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
