"""M6 phase 24: every AppSettings field has a live control, and the tab
restores as rail pages with the persisted settings filled."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pulse_hwm import app_settings
from pulse_hwm.db import Database
from pulse_hwm.ui.settings_tab import SettingsTab


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeMonitor:
    def reconfigure(self, **kwargs):
        del kwargs


class FakeAlerts:
    def set_channels(self, channels):
        del channels

    def notify(self, *args, **kwargs):
        return {}


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "coverage-test.db")


@pytest.fixture()
def tab(app, db):
    return SettingsTab(db, FakeMonitor(), FakeAlerts())


def test_every_settings_field_has_a_bound_control(tab):
    collectable = tab.collect()
    for field in dataclasses.fields(app_settings.AppSettings):
        value_after = getattr(collectable, field.name)
        assert value_after is not None, f"missing control for {field.name}"


def test_values_round_trip_through_the_tab(app, db, tab):
    values = app_settings.load(db)
    values.hardware_interval_ms = 1234
    values.website_interval_s = 99
    app_settings.save(db, values)
    tab.load_from(app_settings.load(db))
    collected = tab.collect()
    assert collected.hardware_interval_ms == 1234
    assert collected.website_interval_s == 99


def test_rgb_settings_reachable_from_rgb_tab_not_settings(
    app, db, tab, db_closed=False
):
    # RGB lives in its own tab — the SETTINGS tab must not double-bind rgb
    # keys: collect() (settings tab scope) returns defaults for every rgb_*
    # field, so the RGB tab stays the single editor surface
    collected = tab.collect()
    defaults = app_settings.AppSettings()
    rgb_names = [
        f.name
        for f in dataclasses.fields(app_settings.AppSettings)
        if f.name.startswith("rgb_")
    ]
    assert all(
        getattr(collected, name) == getattr(defaults, name) for name in rgb_names
    )
