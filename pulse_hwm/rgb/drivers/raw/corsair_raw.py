"""Corsair RAW driver — 0x1B1C devices, first pass.

UNVERIFIED and currently anchor-less: no Corsair device was present on the
scan machine (scripts/rgb_raw_scan.py found none). Everything here comes
from community research (OpenRGB corsair controllers and the classic
"corsair protocol" notes):
  * RGB control channel: vendor usage page 0xFF58 (fallback 0xFF00),
    feature-report transactions of 33 bytes starting at report id 0x07,
    envelope [cmd, data_size, payload...].
  * No byte-level protocol is trusted enough to ship writes yet — set_frame
    fails closed until a capture validates the handshake/brightness/frame
    sequence on a real device.
Probe/open/devices are real and operable as soon as hardware appears.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult
from pulse_hwm.rgb.drivers.raw.base_raw import RawHidDriverBase
from pulse_hwm.rgb.layout import grid_layout
from pulse_hwm.rgb.model import RgbDevice

VID = 0x1B1C
USAGE_PAGE = 0xFF58

EXPERIMENTAL_REASON = (
    "corsair protocol has no validated byte layout yet (raw-protocols phase)"
)


def _load_hid():
    import hid

    return hid


class CorsairRawDriver(RawHidDriverBase):
    driver_id = "corsair_raw"
    name = "Corsair (raw HID)"
    version = "1"
    requires_admin = False
    requires_app = ""
    vid = VID
    pid = 0  # family-wide probe: enumerate(vid, 0) matches every Corsair PID
    usage_page = USAGE_PAGE

    def _find_entry(self):
        if self._hid is None:
            self._hid = _load_hid()
        for entry in self._hid.enumerate(self.vid, self.pid):
            page = entry.get("usage_page") or 0
            if page in (0xFF58, 0xFF00):
                return entry
        return None

    def probe(self) -> ProbeResult:
        if self._hid is None:
            self._hid = _load_hid()
        entries = self._hid.enumerate(self.vid, self.pid)
        if not entries:
            return ProbeResult(
                False, "no Corsair device detected (plug it in — no vendor app needed)"
            )
        # First pass: any Corsair interface counts — the exact RGB usage
        # page may differ per product; open() reports honestly if the pick
        # was wrong.
        return ProbeResult(True)

    def open(self) -> None:
        entry = self._find_entry()
        if entry is None:
            raise RuntimeError("no Corsair collection answered vendor-page probe")
        device = self._hid.device()
        device.open_path(entry["path"])
        self._device = device
        self._path = str(entry.get("path", ""))

    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(
                device_id="corsair:0",
                name="Corsair keyboard",
                driver_id=self.driver_id,
                leds=144,
                layout=grid_layout(18, 8),
                modes=frozenset({"raw:first-pass"}),
            )
        ]

    def set_frame(self, device_id: str, colors: list) -> bool:
        # deliberate: no protocol payload — fail closed with the reason
        _ok, why = self._frame_guard(EXPERIMENTAL_REASON)
        self.last_error = why
        return False
