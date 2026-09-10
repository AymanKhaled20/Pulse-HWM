from __future__ import annotations

import pytest

from pulse_hwm import app_settings
from pulse_hwm.db import Database
from pulse_hwm.util import human_bytes, human_uptime, short_cpu_name, str_to_bool


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "settings.db")


def test_defaults_when_empty(db):
    values = app_settings.load(db)
    assert values.hardware_interval_ms == 1000
    assert values.website_interval_s == 30
    assert values.retention_days == 30


def test_roundtrip(db):
    values = app_settings.AppSettings(
        hardware_interval_ms=500,
        website_interval_s=15,
        website_timeout_s=7.5,
        ssl_warn_days=21,
        retention_days=10,
        sound_enabled=False,
        desktop_enabled=True,
        webhooks_enabled=False,
    )
    app_settings.save(db, values)
    loaded = app_settings.load(db)
    assert loaded == values


def test_corrupt_value_falls_back(db):
    db.set_setting("hardware_interval_ms", "not-a-number")
    values = app_settings.load(db)
    assert values.hardware_interval_ms == 1000


def test_clamp():
    assert app_settings.clamp("hardware_interval_ms", 10) == 250
    assert app_settings.clamp("website_interval_s", 99999) == 3600


def test_human_bytes():
    assert human_bytes(0) == "0.0 B"
    assert human_bytes(500) == "500.0 B"
    assert human_bytes(1024 * 1024).endswith("MB")
    assert human_bytes(None) == "N/A"


def test_human_uptime():
    assert human_uptime(3_900) == "1h 5m"
    assert human_uptime(60) == "1m"
    assert human_uptime(2 * 86400 + 3) == "2d 0h 0m"


def test_str_to_bool():
    assert str_to_bool("true") is True
    assert str_to_bool("false") is False
    assert str_to_bool("", True) is True


def test_cpu_name_clean():
    assert short_cpu_name("Intel(R) Core(TM) i7 CPU @ 2.60GHz") == "Intel Core i7"
    name = short_cpu_name("")
    assert isinstance(name, str) and len(name) > 0
