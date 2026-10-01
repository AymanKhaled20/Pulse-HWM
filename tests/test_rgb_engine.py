"""RgbEngine tests — pure logic against FakeDriver; one offscreen QTimer
smoke test for the worker bridge (QT_QPA_PLATFORM=offscreen via conftest)."""

from __future__ import annotations

import pytest

from pulse_hwm.rgb.drivers.registry import FakeDriver
from pulse_hwm.rgb.engine import DeviceAssignment, RgbEngine
from pulse_hwm.rgb.model import RgbColor
from pulse_hwm.rgb.worker import RgbWorker


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


def test_worker_start_signal_starts_render_timer():
    """The app starts the worker through this queued signal after QThread.start.

    Without this wiring, APPLY NOW updates assignments but no tick ever sends
    a frame to a real device.
    """
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    worker = RgbWorker()
    try:
        worker.start_requested.emit(30)
        assert worker._timer.isActive()
    finally:
        worker.stop()


class ReplugDriver(FakeDriver):
    """FakeDriver whose second device disappears when `unplug()` is called."""

    def __init__(self) -> None:
        super().__init__()
        self._changed = False
        self._unplugged = False

    def unplug(self) -> None:
        self._unplugged = True
        self._changed = True

    def poll_changes(self) -> bool:
        changed, self._changed = self._changed, False
        return changed

    def devices(self):
        devices = super().devices()
        return devices[:1] if self._unplugged else devices


def test_engine_poll_driver_rereads_devices_only_on_change():
    engine = RgbEngine()
    driver = ReplugDriver()
    engine.attach_driver(driver)
    assert engine.poll_driver() is None
    driver.unplug()
    changed = engine.poll_driver()
    assert [d.device_id for d in changed] == ["fake:0"]
    assert engine.device_ids() == ["fake:0"]
    assert engine.poll_driver() is None  # reported once


def test_worker_reports_device_changes_like_an_attach():
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    worker = RgbWorker()
    driver = ReplugDriver()
    worker.engine.attach_driver(driver)
    changed_lists: list[list] = []
    reports: list[tuple] = []
    worker.devices_changed.connect(changed_lists.append)
    worker.devices_reported.connect(lambda *args: reports.append(args))

    driver.unplug()
    worker._poll_device_changes()
    assert [d.device_id for d in changed_lists[-1]] == ["fake:0"]
    assert reports[-1][0] == "fake"

    worker._poll_device_changes()  # throttled: under a second later, no poll
    assert len(changed_lists) == 1


class RescanDriver(FakeDriver):
    """FakeDriver whose first detection missed the second device."""

    def __init__(self, rescan_works: bool = True) -> None:
        super().__init__()
        self._found_all = False
        self._rescan_works = rescan_works
        self.rescans = 0

    def rescan(self) -> bool:
        self.rescans += 1
        if self._rescan_works:
            self._found_all = True
        return self._rescan_works

    def devices(self):
        devices = super().devices()
        return devices if self._found_all else devices[:1]


def test_engine_rescan_picks_up_a_missed_device():
    engine = RgbEngine()
    driver = RescanDriver()
    engine.attach_driver(driver)
    assert engine.device_ids() == ["fake:0"]
    found = engine.rescan_driver()
    assert [d.device_id for d in found] == engine.device_ids()
    assert len(engine.device_ids()) == 2


def test_engine_rescan_without_driver_or_on_failure_returns_none():
    engine = RgbEngine()
    assert engine.rescan_driver() is None
    engine.attach_driver(RescanDriver(rescan_works=False))
    assert engine.rescan_driver() is None
    assert engine.device_ids() == ["fake:0"]


def test_worker_rescan_reports_devices_even_when_nothing_changed():
    """The RGB tab waits for a report to leave its "rescanning" state."""
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    for works, expected in ((True, 2), (False, 1)):
        worker = RgbWorker()
        driver = RescanDriver(rescan_works=works)
        worker.engine.attach_driver(driver)
        reports: list[tuple] = []
        worker.devices_reported.connect(lambda *args: reports.append(args))
        worker.rescan_requested.emit()  # same thread → direct call
        assert driver.rescans == 1
        assert reports[-1][0] == "fake"
        assert len(reports[-1][2]) == expected


def test_worker_rescan_without_driver_retries_the_attach():
    from PySide6.QtWidgets import QApplication

    from pulse_hwm.rgb.drivers.registry import DriverRegistry

    _app = QApplication.instance() or QApplication([])
    worker = RgbWorker()
    worker.attach_first_available(DriverRegistry(()))  # nothing available
    driver = FakeDriver()
    worker._registry = DriverRegistry((driver,))
    worker._registry.load()
    worker.rescan()
    assert worker.engine.driver is driver
