from __future__ import annotations

import time

import pytest

from pulse_hwm.collectors.websites import uptime_percent
from pulse_hwm.db import Database


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test_pulse.db")


def test_sites_crud(db):
    site_id = db.add_site("Example", "https://example.com", expected_status=200)
    rows = db.get_sites()
    assert len(rows) == 1
    assert rows[0]["name"] == "Example"
    db.set_site_enabled(site_id, False)
    assert db.get_sites(include_disabled=False) == []
    db.remove_site(site_id)
    assert db.get_sites() == []


def test_checks_and_uptime(db):
    site_id = db.add_site("Example", "https://example.com")
    now = time.time()
    for i in range(8):
        db.insert_check(site_id, now - 100 + i, 200, 10.0 * i, ok=True)
    db.insert_check(site_id, now - 10, 500, 900.0, ok=False, error="boom")
    assert uptime_percent(db, site_id, 86400) == pytest.approx(8 / 9 * 100)
    other_id = db.add_site("Second", "https://second.com")
    db.insert_check(other_id, now, 200, 1.0, True)
    assert uptime_percent(db, other_id, 86400) == pytest.approx(100.0)
    assert uptime_percent(db, 424242, 86400) is None


def test_uptime_window_exclusion(db):
    site_id = db.add_site("Old", "https://old.com")
    now = time.time()
    db.insert_check(site_id, now - 3 * 86400, 500, 1.0, ok=False)
    # outside the 1-day window → no data
    assert uptime_percent(db, site_id, 86400) is None


def test_seed_default_sites_once(db):
    db.seed_default_sites()
    n_first = len(db.get_sites())
    assert n_first > 0
    db.seed_default_sites()
    assert len(db.get_sites()) == n_first


def test_settings_roundtrip(db):
    db.set_setting("retention_days", "45")
    assert db.get_setting("retention_days", "30") == "45"
    db.set_setting("retention_days", "7")
    assert db.get_setting("retention_days", "30") == "7"


def test_retention_prune(db):
    site_id = db.add_site("Example", "https://example.com")
    now = time.time()
    db.insert_check(site_id, now - 40 * 86400, 200, 1.0, True)
    db.insert_check(site_id, now - 100, 200, 1.0, True)
    db.insert_event(now - 40 * 86400, "INFO", "test", "ancient")
    db.insert_event(now, "INFO", "test", "fresh")
    counts = db.prune(30)
    assert counts["checks"] == 1
    assert counts["events"] == 1
    remaining = [r["ok"] for r in db.checks_since(site_id, 0)]
    assert remaining == [1]
