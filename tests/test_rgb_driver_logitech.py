"""Logitech driver tests with a fake ctypes DLL — no vendor software."""

from __future__ import annotations

import pytest

from pulse_hwm.rgb.drivers.logitech_g import LogitechDriver, _representative
from pulse_hwm.rgb.model import RgbColor


class FakeLedDll:
    """Scripted LogiLed surface."""

    def __init__(self):
        self.calls: list[str] = []
        self.lighting: list[tuple[int, int, int]] = []
        self.target_ok = True
        self.set_lighting_ok = True

    def LogiLedInit(self):
        self.calls.append("init")
        return True

    def LogiLedSetTargetDevice(self, mask):
        self.calls.append(f"target:{mask}")
        return self.target_ok

    def LogiLedSetLighting(self, r, g, b):
        self.lighting.append((r, g, b))
        return self.set_lighting_ok

    def LogiLedShutdown(self):
        self.calls.append("shutdown")
        return True


DUMMY_DLL = object()  # loader returns a real dll object; path check only


@pytest.fixture()
def rigged(tmp_path):
    # probe() now verifies the DLL actually exists — write a dummy
    dll_path = tmp_path / "LogitechLED.dll"
    dll_path.write_bytes(b"not-a-real-dll")
    module = FakeLedDll()
    driver = LogitechDriver(loader=lambda path: module, dll_path=dll_path)
    return driver, module


class TestProbe:
    def test_missing_dll_reports_reason(self, tmp_path):
        driver = LogitechDriver(dll_path=tmp_path / "absent.dll")
        probe = driver.probe()
        assert probe.available is False
        assert "Logitech G HUB" in probe.needs_install

    def test_dll_present_means_available(self, rigged):
        driver, _ = rigged
        assert driver.probe().available is True


class TestOpenClose:
    def test_open_sets_target_device(self, rigged):
        driver, module = rigged
        driver.open()
        assert "target:7" in module.calls

    def test_target_failure_raises(self, rigged):
        driver, module = rigged
        module.target_ok = False
        try:
            driver.open()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass

    def test_close_shuts_down(self, rigged):
        driver, module = rigged
        driver.open()
        driver.close()
        assert "shutdown" in module.calls
        assert driver._dll is None


class TestSetFrame:
    def test_percent_conversion(self, rigged):
        driver, module = rigged
        driver.open()
        ok = driver.set_frame("logitech:all", [RgbColor(255, 0, 128)] * 5)
        assert ok is True
        assert module.lighting[-1] == (100, 0, 50)

    def test_mixed_frame_averages(self, rigged):
        driver, module = rigged
        driver.open()
        mixed = [RgbColor(0, 0, 0)] * 4 + [RgbColor(200, 100, 50)] * 4
        assert driver.set_frame("logitech:all", mixed) is True
        # representative = (100, 50, 25) 0-255 avg → (39, 19, 9) percentages
        assert module.lighting[-1] == (39, 19, 9)

    def test_identical_frame_skipped(self, rigged):
        driver, module = rigged
        driver.open()
        red = [RgbColor(255, 0, 0)] * 3
        driver.set_frame("logitech:all", red)
        before = len(module.lighting)
        driver.set_frame("logitech:all", red)
        assert len(module.lighting) == before

    def test_sdk_false_reports_error(self, rigged):
        driver, module = rigged
        driver.open()
        module.set_lighting_ok = False
        assert driver.set_frame("logitech:all", [RgbColor()] * 2) is False
        assert driver.last_error

    def test_not_open_rejects(self):
        driver = LogitechDriver(loader=lambda _path: FakeLedDll())
        assert driver.set_frame("x", [RgbColor()] * 2) is False


def test_representative_uniform_exact():
    assert _representative([RgbColor(1, 2, 3)] * 4) == RgbColor(1, 2, 3)


def test_representative_mixed_averages():
    rep = _representative([RgbColor(10, 20, 30)] * 2 + [RgbColor(30, 40, 50)] * 2)
    assert (rep.r, rep.g, rep.b) == (20, 30, 40)
