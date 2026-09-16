"""Corsair iCUE driver tests with a fake cuesdk binding."""

from __future__ import annotations

import pytest

from pulse_hwm.rgb.drivers.corsair_icue import CorsairDriver, _representative
from pulse_hwm.rgb.model import RgbColor


@pytest.fixture()
def rigged():
    holder: dict = {}

    def factory():
        class _Sdk:
            def __init__(self):
                self.connected = 0
                self.closed = 0
                self.broadcasted: list[RgbColor] = []
                self.broadcast_ok = True

            def connect(self):
                self.connected += 1

            def broadcast_colors(self, color: RgbColor):
                if self.broadcast_ok:
                    self.broadcasted.append(color)
                    return True
                return False

            def disconnect(self):
                self.closed += 1

        sdk = _Sdk()
        holder["sdk"] = sdk
        return sdk

    return CorsairDriver(sdk_factory=factory), holder


class TestProbe:
    def test_missing_sdk_reports_install_hint(self, monkeypatch):
        import pulse_hwm.rgb.drivers.corsair_icue as module

        monkeypatch.setattr(module, "_load_cuesdk", lambda: None)
        driver = CorsairDriver()
        probe = driver.probe()
        assert probe.available is False
        assert "cuesdk" in probe.reason

    def test_injectable_factory_means_available(self, rigged):
        driver, _ = rigged
        assert driver.probe().available is True


class TestLifecycle:
    def test_open_connects_close_disconnects(self, rigged):
        driver, holder = rigged
        driver.open()
        assert holder["sdk"].connected == 1
        driver.close()
        assert holder["sdk"].closed == 1
        assert driver._sdk is None


class TestSetFrame:
    def test_broadcast_sent(self, rigged):
        driver, holder = rigged
        driver.open()
        ok = driver.set_frame("corsair:all", [RgbColor(10, 20, 30)] * 6)
        assert ok is True
        assert holder["sdk"].broadcasted[-1] == RgbColor(10, 20, 30)

    def test_identical_frame_skipped(self, rigged):
        driver, holder = rigged
        driver.open()
        green = [RgbColor(0, 255, 0)] * 4
        driver.set_frame("corsair:all", green)
        before = len(holder["sdk"].broadcasted)
        driver.set_frame("corsair:all", green)
        assert len(holder["sdk"].broadcasted) == before

    def test_refusal_reports_error(self, rigged):
        driver, holder = rigged
        driver.open()
        holder["sdk"].broadcast_ok = False
        assert driver.set_frame("corsair:all", [RgbColor()] * 2) is False
        assert "refused" in driver.last_error

    def test_not_open_rejects(self):
        driver = CorsairDriver(sdk_factory=lambda: type("S", (), {})())
        assert driver.set_frame("x", [RgbColor()] * 2) is False


def test_representative_simple():
    assert _representative([RgbColor(1, 2, 3)] * 3) == RgbColor(1, 2, 3)

    rep = _representative([RgbColor(0, 0, 0)] + [RgbColor(90, 60, 30)] * 3)
    assert (rep.r, rep.g, rep.b) == (67, 45, 22)
