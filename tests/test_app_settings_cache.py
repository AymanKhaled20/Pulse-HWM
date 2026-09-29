from __future__ import annotations

import time

from pulse_hwm import app_settings
from pulse_hwm.db import Database


def test_repeat_load_skips_the_database(tmp_path, monkeypatch):
    db = Database(tmp_path / "pulse.db")
    app_settings.load(db)  # fills the cache

    calls = {"n": 0}
    real = db.get_all_settings

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(db, "get_all_settings", counting)
    for _ in range(5):
        app_settings.load(db)
    assert calls["n"] == 0
    db.close()


def test_local_write_invalidates_cache(tmp_path):
    db = Database(tmp_path / "pulse.db")
    assert app_settings.load(db).rgb_mode == "off"
    app_settings.save_field(db, "rgb_mode", "reactive")
    assert app_settings.load(db).rgb_mode == "reactive"
    db.close()


def test_cloud_write_invalidates_cache(tmp_path):
    db = Database(tmp_path / "pulse.db")
    assert app_settings.load(db).theme_color == "amber"
    db.adopt_cloud_setting("theme_color", "matrix", time.time() + 60)
    assert app_settings.load(db).theme_color == "matrix"
    db.close()


def test_mutating_returned_settings_does_not_touch_cache(tmp_path):
    db = Database(tmp_path / "pulse.db")
    first = app_settings.load(db)
    first.rgb_mode = "override"  # e.g. the Settings form staging a save
    assert app_settings.load(db).rgb_mode == "off"
    db.close()


def test_out_of_range_values_are_clamped(tmp_path):
    """Synced keys can arrive from another device/version; they must still
    land inside LIMITS instead of e.g. a 0 s website interval."""
    db = Database(tmp_path / "pulse.db")
    db.set_setting("website_interval_s", "0")
    db.set_setting("website_timeout_s", "999")
    values = app_settings.load(db)
    assert values.website_interval_s == app_settings.LIMITS["website_interval_s"][0]
    assert values.website_timeout_s == app_settings.LIMITS["website_timeout_s"][1]
    db.close()


def test_garbage_values_fall_back_to_defaults(tmp_path):
    db = Database(tmp_path / "pulse.db")
    db.set_setting("process_interval_s", "fast")
    db.set_setting("rgb_mode", "disco")
    db.set_setting("rgb_alert_color", "red")
    db.set_setting("rgb_device_assignment", "[1, 2]")
    values = app_settings.load(db)
    defaults = app_settings.AppSettings()
    assert values.process_interval_s == defaults.process_interval_s
    assert values.rgb_mode == defaults.rgb_mode
    assert values.rgb_alert_color == defaults.rgb_alert_color
    assert values.rgb_device_assignment == defaults.rgb_device_assignment
    db.close()
