"""rgb_* settings persistence: round-trip, clamping, and corrupt-value
degradation. Uses a tmp_path SQLite Database — no network, no Qt."""

from __future__ import annotations

from pathlib import Path

import pytest

from pulse_hwm import app_settings
from pulse_hwm.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "settings-test.db")


def test_rgb_defaults_when_never_saved(db: Database):
    values = app_settings.load(db)
    assert values.rgb_mode == "off"
    assert values.rgb_engine_fps == 30
    assert values.rgb_reassert_s == 2
    assert values.rgb_brightness == 100
    assert values.rgb_override_effect == "static"
    assert values.rgb_override_color == "#FFD400"
    assert values.rgb_reactive_source == "cpu"
    assert values.rgb_temp_low_c == 40
    assert values.rgb_temp_high_c == 85
    assert values.rgb_temp_low_color == "#00FF41"
    assert values.rgb_temp_high_color == "#FF3B30"
    assert values.rgb_alert_color == "#FF3B30"
    assert values.rgb_alert_hold_ms == 4000
    assert values.rgb_device_assignment == "{}"
    assert values.rgb_user_effects == "[]"
    assert values.rgb_allow_external_plugins is False
    assert values.rgb_allow_effect_urls is False


def test_rgb_round_trip(db: Database):
    values = app_settings.load(db)
    values.rgb_mode = "override"
    values.rgb_engine_fps = 60
    values.rgb_brightness = 75
    values.rgb_override_color = "#00AACC"
    values.rgb_reactive_source = "gpu"
    values.rgb_temp_high_c = 90
    values.rgb_device_assignment = '{"aula:0": {"effect": "static"}}'
    values.rgb_user_effects = '[{"id": "mine"}]'
    values.rgb_allow_effect_urls = True
    app_settings.save(db, values)

    reloaded = app_settings.load(db)
    assert reloaded.rgb_mode == "override"
    assert reloaded.rgb_engine_fps == 60
    assert reloaded.rgb_brightness == 75
    assert reloaded.rgb_override_color == "#00AACC"
    assert reloaded.rgb_reactive_source == "gpu"
    assert reloaded.rgb_temp_high_c == 90
    assert reloaded.rgb_device_assignment == '{"aula:0": {"effect": "static"}}'
    assert reloaded.rgb_user_effects == '[{"id": "mine"}]'
    assert reloaded.rgb_allow_effect_urls is True


def test_rgb_values_clamped_on_load(db: Database):
    db.set_setting("rgb_engine_fps", "9999")
    db.set_setting("rgb_brightness", "-3")
    db.set_setting("rgb_alert_hold_ms", "1")
    db.set_setting("rgb_temp_high_c", "500")
    values = app_settings.load(db)
    assert values.rgb_engine_fps == 60
    assert values.rgb_brightness == 0
    assert values.rgb_alert_hold_ms == 500
    assert values.rgb_temp_high_c == 150


def test_rgb_corrupt_values_degrade_to_defaults(db: Database):
    db.set_setting("rgb_mode", "chaos")
    db.set_setting("rgb_reactive_source", "disk")
    db.set_setting("rgb_override_color", "red")
    db.set_setting("rgb_temp_low_color", "#12345")
    db.set_setting("rgb_device_assignment", "not json")
    db.set_setting("rgb_user_effects", '{"not": "a list"}')
    db.set_setting("rgb_engine_fps", "fast")
    values = app_settings.load(db)
    assert values.rgb_mode == "off"
    assert values.rgb_reactive_source == "cpu"
    assert values.rgb_override_color == "#FFD400"
    assert values.rgb_temp_low_color == "#00FF41"
    assert values.rgb_device_assignment == "{}"
    assert values.rgb_user_effects == "[]"
    assert values.rgb_engine_fps == 30


def test_rgb_keys_are_device_local():
    # the entire rgb_* surface must stay out of cloud sync: plugin/device
    # ids are meaningless on another machine
    rgb_keys = {k for k in app_settings._ALL_KEYS if k.startswith("rgb_")}
    assert rgb_keys
    assert rgb_keys.isdisjoint(app_settings.SYNCABLE_KEYS)


def test_save_field_accepts_rgb_keys(db: Database):
    # instant-apply path (same contract as alert toggles)
    app_settings.save_field(db, "rgb_mode", "effects")
    assert db.get_setting("rgb_mode") == "effects"


def test_save_field_rejects_unknown_rgb_key(db: Database):
    with pytest.raises(ValueError):
        app_settings.save_field(db, "rgb_typo_key", "x")


def test_all_rgb_fields_have_key_sets():
    # invariant: every rgb_ field on the dataclass is registered in a key
    # set — a missing entry silently never persists (the save() loop
    # iterates _ALL_KEYS via getattr)
    import dataclasses

    field_names = {
        f.name
        for f in dataclasses.fields(app_settings.AppSettings)
        if f.name.startswith("rgb_")
    }
    assert field_names == {k for k in app_settings._ALL_KEYS if k.startswith("rgb_")}


def test_rgb_modes_and_sources_constants():
    assert app_settings.RGB_MODES == ("off", "effects", "reactive", "override")
    assert app_settings.RGB_REACTIVE_SOURCES == ("cpu", "gpu", "mem", "max_temp")
