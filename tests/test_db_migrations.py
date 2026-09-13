from __future__ import annotations

import sqlite3

from pulse_hwm.db import Database, _latest_schema_version


def _make_legacy_db(path) -> None:
    """Simulate a pulse.db from BEFORE migration 1 (no uuid/updated_at)."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE sites (
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
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO sites (name, url, created_at) VALUES ('Old', 'https://old.example', 100.0);
        INSERT INTO settings (key, value) VALUES ('theme_color', 'amber');
        """)
    conn.commit()
    conn.close()


def test_migration_upgrades_legacy_db(tmp_path):
    legacy = tmp_path / "legacy.db"
    _make_legacy_db(legacy)

    db = Database(legacy)
    try:
        # columns exist, uuid backfilled uniquely, timestamps sane
        cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(sites)")}
        assert {"uuid", "updated_at", "deleted"} <= cols
        rows = db._conn.execute(
            "SELECT uuid, updated_at, deleted FROM sites WHERE name = 'Old'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["uuid"] and len(rows[0]["uuid"]) == 32
        assert rows[0]["updated_at"] > 0
        assert int(rows[0]["deleted"]) == 0

        # settings_sync mirrors the legacy settings row
        sync = db._conn.execute(
            "SELECT updated_at FROM settings_sync WHERE key = 'theme_color'"
        ).fetchone()
        assert sync is not None and sync["updated_at"] > 0

        # schema_meta records the applied version
        assert (
            db._conn.execute("SELECT MAX(version) AS v FROM schema_meta").fetchone()[
                "v"
            ]
            == _latest_schema_version()
        )
    finally:
        db.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "fresh.db"
    db = Database(path)
    db.close()
    db2 = Database(path)  # reopen: re-running the runner changes nothing
    try:
        versions = db2._conn.execute(
            "SELECT version FROM schema_meta ORDER BY version"
        ).fetchall()
        assert [v["version"] for v in versions] == [_latest_schema_version()]
    finally:
        db2.close()


def test_add_site_writes_sync_columns(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        site_id = db.add_site(name="X", url="https://x.example")
        rows = db._conn.execute(
            "SELECT uuid, updated_at, deleted FROM sites WHERE id = ?", (site_id,)
        ).fetchall()
        assert len(rows) == 1
        r = rows[0]
        assert r["uuid"] and len(r["uuid"]) == 32
        assert r["updated_at"] > 0
        assert int(r["deleted"]) == 0
    finally:
        db.close()


def test_remove_site_tombstones_instead_of_deleting(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        site_id = db.add_site(name="X", url="https://x.example")
        uuid_before = db._conn.execute(
            "SELECT uuid FROM sites WHERE id = ?", (site_id,)
        ).fetchone()["uuid"]
        db.remove_site(site_id)
        # row survives as a tombstone so other devices learn about the delete
        row = db._conn.execute(
            "SELECT uuid, deleted, enabled, updated_at FROM sites WHERE id = ?",
            (site_id,),
        ).fetchone()
        assert row["uuid"] == uuid_before
        assert int(row["deleted"]) == 1
        # and get_sites hides it
        assert db.get_sites() == []
    finally:
        db.close()


def test_set_setting_bumps_sync_timestamp(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        db.set_setting("theme_color", "amber")
        t1 = db._conn.execute(
            "SELECT updated_at FROM settings_sync WHERE key = 'theme_color'"
        ).fetchone()["updated_at"]
        db.set_setting("theme_color", "paper")
        t2 = db._conn.execute(
            "SELECT updated_at FROM settings_sync WHERE key = 'theme_color'"
        ).fetchone()["updated_at"]
        assert t2 >= t1
        # unknown keys are not tracked
        assert (
            db._conn.execute(
                "SELECT COUNT(*) AS n FROM settings_sync WHERE key = 'nope'"
            ).fetchone()["n"]
            == 0
        )
    finally:
        db.close()
