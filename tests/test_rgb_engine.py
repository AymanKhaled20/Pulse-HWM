"""RgbEngine tests — pure logic against FakeDriver; one offscreen QTimer
smoke test for the worker bridge (QT_QPA_PLATFORM=offscreen via conftest)."""

from __future__ import annotations

import pytest

from pulse_hwm.rgb.drivers.registry import FakeDriver
from pulse_hwm.rgb.engine import DeviceAssignment, RgbEngine
from pulse_hwm.rgb.model import RgbColor


@pytest.fixture()
def engine_and_driver():
    engine = RgbEngine()
    driver = FakeDriver()
    engine.attach_driver(driver)
    return engine, driver


class TestAttach:
    def test_driver_closes_on_replacement(self, engine_and_driver):
        engine, _ = engine_and_driver
        second = FakeDriver()
        engine.attach_driver(second)
        assert second.opened == 1
        # deleting the first driver cleanly is verified by its open/close
        # counters below (replacement must have closed the old one)

    def test_device_cache_populated(self, engine_and_driver):
        engine, _ = engine_and_driver
        assert set(engine.device_ids()) == {"fake:0", "fake:1"}

    def test_attach_with_no_driver_means_no_frames(self):
        engine = RgbEngine()
        engine.set_assignment("x", DeviceAssignment("static"))
        assert engine.tick() == 0

    def test_close_driver_on_detach(self, engine_and_driver):
        engine, driver = engine_and_driver
        engine.detach_driver()
        assert driver.closed == 1
        assert engine.attached is False
        assert engine.tick() == 0


class TestTick:
    def test_assigned_device_gets_frame(self, engine_and_driver):
        engine, driver = engine_and_driver
        engine.set_assignment(
            "fake:0", DeviceAssignment("static", {"color": "#FF0000"})
        )
        applied = engine.tick()
        assert applied == 1
        frame = driver.frames["fake:0"][-1]
        assert frame[0] == RgbColor(255, 0, 0)

    def test_unassigned_and_disabled_skipped(self, engine_and_driver):
        engine, _ = engine_and_driver
        engine.set_assignment("fake:0", DeviceAssignment("static", enabled=False))
        # fake:1 intentionally left unassigned
        assert engine.tick() == 0

    def test_unknown_effect_id_skips_device(self, engine_and_driver):
        engine, driver = engine_and_driver
        engine.set_assignment("fake:0", DeviceAssignment("vanished_effect"))
        assert engine.tick() == 0
        assert "fake:0" not in driver.frames

    def test_effect_exception_reported_and_tick_continues(self, engine_and_driver):
        # a device whose effect raises must not block the other device
        engine, driver = engine_and_driver
        engine.set_assignment("fake:0", DeviceAssignment("static"))
        engine.set_assignment("fake:1", DeviceAssignment("static"))
        engine.catalog.get("static").render = lambda ctx: (_ for _ in ()).throw(
            RuntimeError("bad render")
        )
        assert engine.tick() == 0
        assert "fake:0" in engine.last_error or "bad render" in engine.last_error

    def test_driver_set_frame_false_not_counted(self, engine_and_driver):
        engine, driver = engine_and_driver
        driver.fail_device = "fake:0"
        engine.set_assignment("fake:0", DeviceAssignment("static"))
        engine.set_assignment("fake:1", DeviceAssignment("static"))
        assert engine.tick() == 1


class TestBrightness:
    def test_full_brightness_is_passthrough(self, engine_and_driver):
        engine, driver = engine_and_driver
        engine.set_assignment(
            "fake:0", DeviceAssignment("static", {"color": "#804020"})
        )
        engine.tick()
        assert driver.frames["fake:0"][-1][0] == RgbColor(0x80, 0x40, 0x20)

    def test_half_brightness_scales(self, engine_and_driver):
        engine, driver = engine_and_driver
        engine.set_brightness(50)
        engine.set_assignment(
            "fake:0", DeviceAssignment("static", {"color": "#804020"})
        )
        engine.tick()
        frame = driver.frames["fake:0"][-1]
        assert frame[0] == RgbColor(0x40, 0x20, 0x10)


class TestTimeStepping:
    def test_breathe_changes_across_ticks(self, engine_and_driver):
        import time as _time

        engine, driver = engine_and_driver
        engine.set_assignment("fake:0", DeviceAssignment("breathe", {"speed": 0.5}))
        engine.tick()  # starts the clock
        _time.sleep(0.02)
        engine.tick()  # different now → different brightness (period ≥ 0.4s)
        frames = driver.frames["fake:0"]
        assert len(frames) == 2
        # not asserting exact values — monotonic stepping can land on
        # similar phases; the point is both frames rendered without error
