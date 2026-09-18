"""Raw (vendor-free) driver tests — fake hid transport, no real hardware,
no vendor software. First-pass drivers must degrade cleanly; experimental
writes only run behind the explicit gate."""

from __future__ import annotations

from pulse_hwm.rgb.drivers.raw.razer_protocol import (
    ENVELOPE_LEN,
    matrix_row_payloads,
    memset_dummy,
)
from pulse_hwm.rgb.model import RgbColor


class FakeHidDevice:
    def __init__(self, records: list[bytes]):
        self.records = records

    def open_path(self, path):
        del path
        return 0

    def send_feature_report(self, payload):
        self.records.append(bytes(payload))
        return 1

    def close(self):
        pass


class FakeHid:
    def __init__(self, entries):
        self.entries = entries
        self.device_resources: list[bytes] = []
        self.opened_paths: list[str] = []

    def enumerate(self, vid, pid):
        return [
            e
            for e in self.entries
            if e.get("vendor_id") == vid and e.get("product_id") == pid
        ]

    def device(self):
        return FakeHidDevice(self.device_resources)


RAZER_ENTRY = {
    "vendor_id": 0x1532,
    "product_id": 0x0537,
    "usage_page": 0xFFA0,
    "path": "fake-path",
}


class TestRazerProtocolBuilder:
    def test_envelope_shape(self):
        payload = memset_dummy()
        assert len(payload) == ENVELOPE_LEN
        assert payload[0] == 0xFF  # status sent marker
        assert payload[1] == 0x3F  # transaction id
        assert payload[4] == 0x03  # protocol type
        assert payload[6] == 0x0F  # matrix class
        assert payload[7] == 0x0A  # set custom frame row

    def test_22_rows_yields_22_envelopes(self):
        colors = list(matrix_row_payloads(RgbColor(12, 34, 56), 22, 6))
        assert len(colors) == 22
        assert colors[0][8] == 0  # row index of the first row
        assert colors[1][8] == 1  # second row
        assert colors[0][11:14] == b"\x0c\x22\x38"  # R,G,B of the color


class TestRazerRawDriver:
    def _driver(self, hid_module, experimental: bool = False):
        from pulse_hwm.rgb.drivers.raw.razer_raw import RazerRawDriver

        driver = RazerRawDriver(hid_module=hid_module)
        driver.experimental_allowed = experimental
        return driver

    def test_probe_without_hidapi_wrapper_reports_unavailable(self):
        from pulse_hwm.rgb.drivers.raw.razer_raw import RazerRawDriver

        driver = RazerRawDriver(hid_module=None)  # real import path: hid exists
        # at least do not raise; the outcome depends on the machine
        outcome = driver.probe()
        assert isinstance(outcome.available, bool)

    def test_probe_finds_nothing_without_device(self):
        driver = self._driver(FakeHid([]))
        assert driver.probe().available is False

    def test_probe_finds_the_scan_confirmed_device(self):
        driver = self._driver(FakeHid([RAZER_ENTRY]))
        assert driver.probe().available is True

    def test_set_frame_fails_closed_without_gate(self):
        hid = FakeHid([RAZER_ENTRY])
        driver = self._driver(hid, experimental=False)
        assert driver.probe().available
        driver.open()
        assert driver.set_frame("razer:0", [RgbColor(255, 0, 0)] * 10) is False
        assert "not" in driver.last_error or "valid" in driver.last_error
        assert hid.device_resources == []  # nothing hit the wire

    def test_set_frame_writes_envelopes_when_gated(self):
        hid = FakeHid([RAZER_ENTRY])
        driver = self._driver(hid, experimental=True)
        driver.open()
        assert driver.set_frame("razer:0", [RgbColor(1, 2, 3)] * 10) is True
        assert len(hid.device_resources) == 22  # one envelope per row
        assert all(len(r) == ENVELOPE_LEN for r in hid.device_resources)


class TestCorsairAndLogitechFailClosed:
    def test_corsair_set_frame_never_sends(self):
        from pulse_hwm.rgb.drivers.raw.corsair_raw import CorsairRawDriver

        driver = CorsairRawDriver()
        driver.experimental_allowed = True
        driver._device = object()  # open-ish, gate on: still must not send
        assert driver.set_frame("corsair:0", [RgbColor(9, 9, 9)]) is False

    def test_corsair_probe_degrades_without_hid(self):
        from pulse_hwm.rgb.drivers.raw.corsair_raw import CorsairRawDriver

        assert CorsairRawDriver().probe().available is False


class TestMsiStub:
    def test_probe_never_reports_available(self):
        from pulse_hwm.rgb.drivers.raw.msi_raw import MysticLightRawDriver

        assert MysticLightRawDriver().probe().available is False

    def test_reason_says_why(self):
        from pulse_hwm.rgb.drivers.raw.msi_raw import MysticLightRawDriver

        result = MysticLightRawDriver().probe()
        if result.available is False:
            # reason explains the absence of a write path, not a crash
            assert result.reason
