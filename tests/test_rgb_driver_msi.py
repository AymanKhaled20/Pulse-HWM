"""MSI Mystic Light driver tests with a scripted ctypes surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from pulse_hwm.rgb.drivers.msi_mystic import (
    MSI_PALETTE,
    MysticLightDriver,
    nearest_palette_index,
)
from pulse_hwm.rgb.model import RgbColor


class FakeMsiDll:
    def __init__(self):
        self.init_rc = 0
        self.set_ok = True
        self.calls: list[tuple[int, int, int]] = []

    def MSC_MysticLight_Initialize(self):
        return self.init_rc

    def MSC_SetLedColor(self, device, zone, color_index):
        # int() because ctypes.c_int wrappers arrive when the driver uses
        # explicit c_int coercion; real SDK reads them identically
        self.calls.append((int(device), int(zone), int(color_index)))
        return self.set_ok

    def MSC_MysticLight_Disconnect(self):
        return 0


@pytest.fixture()
def rigged(tmp_path: Path):
    dll_path = tmp_path / "MysticLight_SDK.dll"  # probe checks existence
    dll_path.write_bytes(b"fake")
    module = FakeMsiDll()
    driver = MysticLightDriver(loader=lambda path: module, dll_path=dll_path)
    return driver, module


class TestNearestPalette:
    def test_exact_palette_color(self):
        assert nearest_palette_index(RgbColor(0, 255, 0)) == 3  # GREEN slot

    def test_off_maps_to_black(self):
        assert nearest_palette_index(RgbColor(0, 0, 0)) == 0

    def test_white(self):
        assert nearest_palette_index(RgbColor(255, 255, 255)) == 10

    def test_close_enough_quotes(self):
        amber = RgbColor.from_hex("#FFD400")
        assert nearest_palette_index(amber) == 4  # YELLOW slot


class TestProbe:
    def test_missing_dll(self, tmp_path):
        driver = MysticLightDriver(dll_path=tmp_path / "absent.dll")
        probe = driver.probe()
        assert probe.available is False
        assert "MSI Center" in probe.needs_install


class TestLifecycle:
    def test_init_success_then_disconnect(self, rigged):
        driver, module = rigged
        driver.open()
        driver.close()
        assert driver._dll is None

    def test_init_failure_raises(self, rigged):
        driver, module = rigged
        module.init_rc = -1
        try:
            driver.open()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass

    def test_not_open_rejects(self):
        driver = MysticLightDriver(loader=lambda _p: FakeMsiDll())
        assert driver.set_frame("x", [RgbColor()] * 2) is False


class TestSetFrame:
    def test_static_palette_write(self, rigged):
        driver, module = rigged
        driver.open()
        ok = driver.set_frame("msi:all", [RgbColor.from_hex("#00FF41")] * 3)
        assert ok is True
        assert module.calls[-1] == (-1, -1, 3)

    def test_identical_frame_skipped(self, rigged):
        driver, module = rigged
        driver.open()
        driver.set_frame("msi:all", [RgbColor(0, 255, 0)] * 3)
        before = len(module.calls)
        driver.set_frame("msi:all", [RgbColor(0, 255, 0)] * 3)
        assert len(module.calls) == before

    def test_sdk_false_reports_error(self, rigged):
        driver, module = rigged
        driver.open()
        module.set_ok = False
        assert driver.set_frame("msi:all", [RgbColor(255, 0, 0)] * 2) is False
        assert driver.last_error

    def test_off_frame_maps_to_black_slot(self, rigged):
        driver, module = rigged
        driver.open()
        driver.set_frame("msi:all", [RgbColor(0, 0, 0)] * 2)
        assert module.calls[-1][2] == 0


def test_palette_has_16_slots():
    assert len(MSI_PALETTE) == 16
