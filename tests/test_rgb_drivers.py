"""Driver registry tests: discovery, isolation of broken drivers, frame
recording against FakeDriver."""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult
from pulse_hwm.rgb.drivers.registry import DriverRegistry, FakeDriver
from pulse_hwm.rgb.model import RgbColor, RgbDevice


class BrokenCtor(FakeDriver):
    driver_id = "broken_ctor"

    def __init__(self) -> None:
        raise RuntimeError("boom")


class BrokenProbe(FakeDriver):
    driver_id = "broken_probe"

    def probe(self) -> ProbeResult:
        raise RuntimeError("probe boom")


class UnavailableDriver(FakeDriver):
    driver_id = "unavailable"

    def probe(self) -> ProbeResult:
        return ProbeResult(False, "vendor app not running", needs_install="SomeApp")


class EmptyIdDriver(FakeDriver):
    driver_id = ""


class TestRegistry:
    def test_available_drivers_listed(self):
        registry = DriverRegistry((FakeDriver,))
        drivers = registry.load()
        assert [d.driver_id for d in drivers] == ["fake"]

    def test_broken_constructor_isolated(self):
        registry = DriverRegistry((FakeDriver, BrokenCtor))
        drivers = registry.load()
        assert [d.driver_id for d in drivers] == ["fake"]
        assert any("BrokenCtor" in e for e in registry.last_errors)

    def test_empty_driver_id_rejected(self):
        registry = DriverRegistry((EmptyIdDriver,))
        assert registry.load() == []
        assert registry.last_errors

    def test_available_filters_probe_failures(self):
        registry = DriverRegistry((FakeDriver, UnavailableDriver))
        registry.load()
        available = registry.available()
        assert [d.driver_id for d in available] == ["fake"]

    def test_probe_exception_isolated(self):
        registry = DriverRegistry((FakeDriver, BrokenProbe))
        registry.load()
        assert [d.driver_id for d in registry.available()] == ["fake"]

    def test_get_round_trip(self):
        registry = DriverRegistry((FakeDriver,))
        registry.load()
        assert registry.get("fake").name == "Fake (test)"
        assert registry.get("nope") is None


class TestFakeDriver:
    def test_frames_recorded(self):
        driver = FakeDriver()
        driver.open()
        ok = driver.set_frame("fake:0", [RgbColor(255, 0, 0)] * 8)
        assert ok is True
        assert len(driver.frames["fake:0"]) == 1
        assert driver.frames["fake:0"][0][0] == RgbColor(255, 0, 0)

    def test_reports_two_devices(self):
        driver = FakeDriver()
        assert len(driver.devices()) == 2

    def test_device_shape(self):
        device = FakeDriver().devices()[0]
        assert isinstance(device, RgbDevice)
        assert device.leds == 8

    def test_fail_device_returns_false(self):
        driver = FakeDriver()
        driver.fail_device = "fake:1"
        assert driver.set_frame("fake:1", []) is False
        assert driver.set_frame("fake:0", []) is True
