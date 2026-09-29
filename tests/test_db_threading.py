from __future__ import annotations

import threading
import time

from pulse_hwm.db import Database


def test_concurrent_reads_and_writes_never_raise(tmp_path):
    """The UI, collector and sync threads share ONE sqlite connection. Reads
    used to skip the lock, which can raise mid-commit under contention."""
    db = Database(tmp_path / "pulse.db")
    site_id = db.add_site("Probe", "https://example.com")
    errors: list[BaseException] = []
    rounds = 200

    def writer() -> None:
        try:
            for i in range(rounds):
                db.insert_check(site_id, time.time(), 200, 12.0, True)
                db.set_setting("theme_color", f"c{i}")
                db.insert_hardware_samples([(time.time(), "cpu", float(i))])
        except BaseException as exc:  # collected, asserted below
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(rounds):
                db.get_sites()
                db.latest_check(site_id)
                db.checks_since(site_id, 0)
                db.get_setting("theme_color")
                db.get_settings_with_ts()
                db.hardware_series("cpu", 0)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(3)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    db.close()
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)


def test_update_site_bumps_updated_at_for_sync(tmp_path):
    """Editing a site must advance updated_at, or the LWW sync engine
    thinks the old cloud copy is just as new and never pushes the edit."""
    db = Database(tmp_path / "pulse.db")
    site_id = db.add_site("Old", "https://old.example")
    before = float(db.get_sites()[0]["updated_at"])
    time.sleep(0.01)

    db.update_site(site_id, name="New", url="https://new.example", keyword="ok")

    row = db.get_sites()[0]
    assert row["name"] == "New"
    assert row["url"] == "https://new.example"
    assert row["keyword"] == "ok"
    assert float(row["updated_at"]) > before
    db.close()


def test_adopt_cloud_setting_keeps_newer_local_write(tmp_path):
    db = Database(tmp_path / "pulse.db")
    db.set_setting("theme_color", "local")
    stale_cloud_ts = time.time() - 60

    assert db.adopt_cloud_setting("theme_color", "cloud", stale_cloud_ts) is False
    assert db.get_setting("theme_color") == "local"

    assert db.adopt_cloud_setting("theme_color", "cloud", time.time() + 60) is True
    assert db.get_setting("theme_color") == "cloud"
    db.close()
