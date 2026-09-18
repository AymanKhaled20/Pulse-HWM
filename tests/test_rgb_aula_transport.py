"""AULA direct-mode transport tests with a scripted fake hidapi — no real
device. Covers channel discovery (0x06 keepalive), frame encoding,
dedupe+keepalive floor, and degradation. Time is injectable."""

from __future__ import annotations

import pytest

from pulse_hwm.rgb.drivers.aula_f75 import (
    SINOWEALTH_LED_COUNT,
    AulaDriver,
    build_direct_frame,
)
from pulse_hwm.rgb.model import RgbColor

FAKE_KEEPALIVE_BLOB = (b"\x06\x08" + bytes(384))[:386]


class FakeHidDevice:
    """One hidapi collection handle. get_feature_report(0x06, 520) succeeds
    only when `direct_channel` is armed (simulates Col06 finding)."""

    def __init__(self):
        self.opened_path: bytes | None = None
        self.closed = 0
        self.frames: list[bytes] = []
        self.direct_channel = False
        self.fail_sends = False

    def open_path(self, path) -> None:
        self.opened_path = path

    def get_feature_report(self, report_id: int, size: int) -> list[int]:
        del size
        if report_id == 0x06 and self.direct_channel:
            return list(FAKE_KEEPALIVE_BLOB)
        raise OSError("read error")

    def send_feature_report(self, data: bytes) -> int:
        if self.fail_sends:
            return -1
        self.frames.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.closed += 1


class FakeHidModule:
    """enumerate() hands out one FakeHidDevice PER PATH (as real hardware
    does); the direct entry answers the 0x06 keepalive, others refuse."""

    def __init__(self, direct_on: str | None = "Col06"):
        self.last_vid = self.last_pid = 0
        self._devices: dict[bytes, FakeHidDevice] = {}
        self.entries: list[dict] = []
        for name in ("Col01", "Col02", "Col06"):
            handle = FakeHidDevice()
            if name == direct_on:
                handle.direct_channel = True
            path = f"kb-{name}".encode()
            self._devices[path] = handle
            self.entries.append({"path": path, "usage_page": 0x0001})

    def enumerate(self, vendor_id: int, product_id: int) -> list:
        self.last_vid, self.last_pid = vendor_id, product_id
        return self.entries

    def device(self) -> FakeHidDevice:
        # a fresh handle for every device() call, like faking hidapi's
        # per-open state; the last open_path result is introspectable via
        # driver._last_open_path (set by the driver on open)
        return _FreshHandle(self)


class _FreshHandle:
    """Returns the FakeHidDevice whose open_path was called, by wrapping
    open_path and forwarding reads/writes to that device."""

    def __init__(self, module: "FakeHidModule"):
        self._module = module
        self._handle: FakeHidDevice | None = None

    def open_path(self, path) -> None:
        self._handle = self._module._devices[bytes(path)]
        self._handle.opened_path = path
        self._module._last_opened_handle = self._handle

    def get_feature_report(self, *args, **kwargs) -> list[int]:
        return self._handle.get_feature_report(*args, **kwargs)

    def send_feature_report(self, *args, **kwargs) -> int:
        return self._handle.send_feature_report(*args, **kwargs)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()


@pytest.fixture()
def armed() -> tuple[AulaDriver, FakeHidDevice]:
    module = FakeHidModule()
    driver = AulaDriver(hid_module=module)
    driver.open()
    return driver, module._last_opened_handle


class TestDirectFrame:
    def test_header_and_size(self):
        frame = build_direct_frame([])
        assert len(frame) == 520
        assert frame[:8] == bytes((0x06, 0x08, 0x00, 0x00, 0x01, 0x00, 0x7A, 0x01))

    def test_uniform_color_all_126_leds(self):
        frame = build_direct_frame([RgbColor(255, 0, 0)] * SINOWEALTH_LED_COUNT)
        for index in range(SINOWEALTH_LED_COUNT):
            base = 8 + index * 3
            assert frame[base : base + 3] == bytes((255, 0, 0))

    def test_partial_colors_pad_black(self):
        frame = build_direct_frame([RgbColor(1, 2, 3)] * 4)
        first_rgb = frame[8 : 8 + 12]
        assert first_rgb == bytes((1, 2, 3)) * 4
        assert frame[8 + 4 * 3 : 8 + 126 * 3] == bytes((126 - 4) * 3)


class TestOpen:
    def test_finds_direct_collection(self, armed):
        driver, fake_device = armed
        assert driver._device is not None
        assert fake_device.opened_path == b"kb-Col06"

    def test_opens_none_when_no_collection_answers(self):
        module = FakeHidModule()
        for handle in module._devices.values():
            handle.direct_channel = False
        driver = AulaDriver(hid_module=module)
        driver.open()
        assert driver._device is None
        assert "0x06" in driver.last_error

    def test_open_without_probe_raises(self):
        driver = AulaDriver(hid_module=FakeHidModule())
        driver._hid = None
        try:
            driver.open()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


class TestProbe:
    def test_simulates_missing_hidapi(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "hid", None)
        driver = AulaDriver(hid_module=None)
        probe = driver.probe()
        assert probe.available is False
        assert "hidapi" in probe.reason

    def test_no_plugged_keyboard(self):
        module = FakeHidModule()
        module.entries = []
        driver = AulaDriver(hid_module=module)
        probe = driver.probe()
        assert probe.available is False
        assert "no AULA" in probe.reason


class TestSetFrame:
    def test_writes_full_direct_frame(self, armed):
        driver, fake_device = armed
        ok = driver.set_frame("aula:0", [RgbColor(255, 0, 0)] * 10)
        assert ok is True
        assert len(fake_device.frames) == 1
        frame = fake_device.frames[0]
        assert len(frame) == 520
        assert frame[8:11] == bytes((255, 0, 0))
        assert frame[8 + 125 * 3 : 8 + 125 * 3 + 3] == bytes((255, 0, 0))

    def test_identical_frame_skipped_before_keepalive(self):
        module = FakeHidModule()
        driver = AulaDriver(hid_module=module, clock=lambda: 0.0)
        driver.open()
        red = [RgbColor(255, 0, 0)] * 10
        assert driver.set_frame("aula:0", red) is True
        first = len(module._last_opened_handle.frames)
        assert driver.set_frame("aula:0", red) is True  # 0.0s < 0.7s floor
        assert len(module._last_opened_handle.frames) == first

    def test_send_failure_false_with_error(self, armed):
        driver, fake_device = armed
        fake_device.fail_sends = True
        assert driver.set_frame("aula:0", [RgbColor()] * 5) is False
        assert "feature write" in driver.last_error

    def test_closed_driver_rejects(self):
        driver = AulaDriver(hid_module=FakeHidModule())
        assert driver.set_frame("aula:0", [RgbColor()] * 2) is False

    def test_close_releases(self, armed):
        driver, fake_device = armed
        driver.close()
        assert fake_device.closed >= 1  # probe may have already reset handle state
        assert driver._device is None
        driver.close()  # idempotent


class TestModeledDevices:
    def test_one_device_with_layout(self, armed):
        driver, _ = armed
        devices = driver.devices()
        assert len(devices) == 1
        device = devices[0]
        assert device.device_id == "aula:0"
        assert device.leds == SINOWEALTH_LED_COUNT
        assert device.layout is not None
        assert device.layout.led_count == len(device.layout.key_names)
