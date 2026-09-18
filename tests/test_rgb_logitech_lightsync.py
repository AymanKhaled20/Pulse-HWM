"""G102/G203 LIGHTSYNC driver tests — fake hid transport, no hardware.

Covers the verified-live protocol shape (enable on open, exact 20-byte
static frames, whole-device dedup) without hitting the mouse."""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult
from pulse_hwm.rgb.drivers.logitech_lightsync import (
    REPORT_LEN,
    LogitechLightsyncDriver,
    build_enable_frame,
    build_static_frame,
)
from pulse_hwm.rgb.model import RgbColor

G102_ENTRY = {
    "vendor_id": 0x046D,
    "product_id": 0xC092,
    "usage_page": 0xFF00,
    "usage": 0x0002,
    "interface_number": 1,
    "path": "fake-path",
}


class FakeHidDevice:
    def __init__(self, records: list[bytes]):
        self.records = records

    def open_path(self, path):
        del path
        return 0

    def write(self, payload):
        self.records.append(bytes(payload))
        return REPORT_LEN

    def close(self):
        pass


class FakeHid:
    def __init__(self, entries):
        self.entries = entries
        self.records: list[bytes] = []

    def enumerate(self, vid, pid):
        return [
            e
            for e in self.entries
            if e.get("vendor_id") == vid and e.get("product_id") == pid
        ]

    def device(self):
        return FakeHidDevice(self.records)


class TestFrames:
    def test_enable_frame_shape(self):
        frame = build_enable_frame()
        assert len(frame) == REPORT_LEN
        assert frame[0] == 0x11
        assert list(frame[1:4]) == [0xFF, 0x0E, 0x50]

    def test_static_frame_shape(self):

        frame = build_static_frame(RgbColor(255, 0, 0))
        assert len(frame) == REPORT_LEN  # OS rejects wrong sizes
        assert frame[0] == 0x11  # long report
        assert frame[1] == 0xFF  # recipient device 0xFF
        assert frame[2] == 0x0E  # feature index
        assert frame[3] == 0x10  # SetMode
        assert frame[5] == 0x01  # static mode id
        assert (frame[6], frame[7], frame[8]) == (255, 0, 0)
        assert frame[9] == 0x02  # static tail
        assert frame[16] == 0x01  # end marker


class TestDriverLifecycle:
    def _driver(self, entries, cert_probe=True):
        driver = LogitechLightsyncDriver(hid_module=FakeHid(entries))
        return driver

    def test_probe_matches_own_collection_only(self):
        driver = self._driver([G102_ENTRY])
        assert driver.probe() == ProbeResult(True)
        wrong = dict(G102_ENTRY, usage=0x0001)
        assert self._driver([wrong]).probe().available is False

    def test_open_sends_enable_then_frame_writes_exact_length(self):
        hid = FakeHid([G102_ENTRY])
        driver = LogitechLightsyncDriver(hid_module=hid)
        driver.open()
        assert len(hid.records) == 1  # enable on open
        assert hid.records[0][0] == 0x11
        ok = driver.set_frame("x", [RgbColor(12, 34, 56)])
        assert ok is True
        frame = hid.records[-1]
        assert len(frame) == REPORT_LEN
        assert frame[6:9] == bytes((12, 34, 56))

    def test_set_frame_dedupes_identical_colors(self):
        hid = FakeHid([G102_ENTRY])
        driver = LogitechLightsyncDriver(hid_module=hid)
        driver.open()
        before = len(hid.records)
        assert driver.set_frame("x", [RgbColor(1, 2, 3)]) is True
        mid = len(hid.records)
        # same color again -> no traffic
        assert driver.set_frame("x", [RgbColor(1, 2, 3)]) is True
        assert len(hid.records) == mid > before

    def test_set_frame_before_open_fails_clean(self):
        driver = self._driver([G102_ENTRY])
        ok = driver.set_frame("x", [RgbColor(1, 2, 3)])
        assert ok is False
        assert driver.last_error

    def test_close_resets_state(self):
        hid = FakeHid([G102_ENTRY])
        driver = LogitechLightsyncDriver(hid_module=hid)
        probe_result = driver.probe()
        assert probe_result.available is True
        driver.open()
        driver.close()
        assert driver.set_frame("x", [RgbColor(1, 1, 1)]) is False
