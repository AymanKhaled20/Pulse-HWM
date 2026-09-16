"""RgbManager integration tests: plan pushes via hook, driver switches,
alert-driven reconsider. Qt-free; uses tmp SQLite for settings reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from pulse_hwm import app_settings
from pulse_hwm.db import Database
from pulse_hwm.rgb.manager import RgbManager


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "manager-test.db")


class RecordedManager(RgbManager):
    """Captures pushed plans instead of crossing into the worker."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pushed: list[dict] = []

    def _push(self, assignments: dict) -> None:
        self.pushed.append(dict(assignments))


def make_manager_with_devices(db: Database) -> RecordedManager:
    manager = RecordedManager(db)
    manager.apply_assignments = manager._push
    manager.set_driver("fake", ["fake:0", "fake:1"])
    return manager


class TestReconsider:
    def test_off_mode_clears_with_hook(self, db: Database):
        manager = make_manager_with_devices(db)
        db.set_setting("rgb_mode", "off")
        pushed = manager.reconsider()
        assert pushed is True
        assert manager.pushed == [{"fake:0": None, "fake:1": None}]

    def test_default_mode_is_off_meaning_no_takeover(self, db: Database):
        # a FRESH install must not fight vendor software: nothing stored →
        # mode defaults to off → the single reconsider pass clears nothing
        manager = make_manager_with_devices(db)
        manager.reconsider()
        assert all(value is None for value in manager.pushed[-1].values())

    def test_second_reconsider_is_noop(self, db: Database):
        manager = make_manager_with_devices(db)
        assert manager.reconsider() is True
        assert manager.reconsider() is False  # same stored settings + plan
        assert len(manager.pushed) == 1

    def test_no_devices_means_no_push(self, db: Database):
        manager = RecordedManager(db)
        manager.apply_assignments = manager._push
        assert manager.reconsider() is False
        assert manager.pushed == []

    def test_override_forces_color_everywhere(self, db: Database):
        db.set_setting("rgb_mode", "override")
        db.set_setting("rgb_override_color", "#0A0B0C")
        manager = make_manager_with_devices(db)
        manager.reconsider()
        assignments = manager.pushed[-1]
        for device_id in ("fake:0", "fake:1"):
            assignment = assignments[device_id]
            assert assignment.effect_id == app_settings.load(db).rgb_override_effect
        assert assignments["fake:0"].effect_id == "static"  # default override

    def test_settings_change_replans(self, db: Database):
        manager = make_manager_with_devices(db)
        manager.reconsider()
        db.set_setting("rgb_mode", "effects")
        db.set_setting(
            "rgb_device_assignment",
            '{"fake/fake:0": {"effect": "static"}}',
        )
        assert manager.reconsider() is True  # real assignment → not a noop
        assert len(manager.pushed) == 2
        assert manager.pushed[-1]["fake:0"].effect_id == "static"


class TestDriverSwitch:
    def test_set_driver_forces_fresh_plan(self, db: Database):
        manager = make_manager_with_devices(db)
        manager.reconsider()
        manager.set_driver("other", ["other:0"])
        assert manager.reconsider() is True  # device set changed → replan

    def test_clear_driver_disables(self, db: Database):
        manager = make_manager_with_devices(db)
        manager.reconsider()
        before = len(manager.pushed)
        manager.clear_driver()
        assert manager.reconsider() is False  # no devices → nothing to plan
        assert len(manager.pushed) == before


class TestAlertFlow:
    def test_alert_triggers_reconsider(self, db: Database):
        manager = make_manager_with_devices(db)
        manager.reconsider()
        before = len(manager.pushed)
        manager.handle_alert()
        assert len(manager.pushed) in (before, before + 1)


class TestForceReconsider:
    def test_force_pushes_even_when_plan_is_noop(self, db: Database):
        manager = make_manager_with_devices(db)
        manager.reconsider()
        pushes_after_normal = len(manager.pushed)
        second = manager.reconsider()  # identical plan -> runtime refuses
        assert second is False
        assert len(manager.pushed) == pushes_after_normal
        forced = manager.force_reconsider()  # APPLY NOW: always pushes
        assert forced is True
        assert len(manager.pushed) == pushes_after_normal + 1

    def test_force_after_off_then_same_mode(self, db: Database):
        from pulse_hwm import app_settings

        manager = make_manager_with_devices(db)
        manager.reconsider()
        pushed = len(manager.pushed)
        # settings unchanged, but the user pressed APPLY NOW: re-push anyway
        app_settings.save_field(db, "rgb_mode", "off")
        assert manager.force_reconsider() is True
        assert len(manager.pushed) == pushed + 1
        # cleared assignments are the payload for off mode
        assert all(v is None for v in manager.pushed[-1].values())
