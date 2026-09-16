"""Logitech RAW driver — HID++ (usage page 0x0059), first pass.

On the scan machine the G HUBuntime exposes VID 0x046D PID 0xC092 with a
0x59 usage-page interface — the Logitech HID++ channel, and it opens
cleanly even while G HUB is running (the two can share).

UNVERIFIED protocol plan (openrazer-free, per the public Logitech "Gaming
LED"/HID++ community notes):
  * short report [0x10, device_index, feature_index, func<<4, params...]
  * the LED feature index is per-device — discovered from root feature
    0x0000 function 0x0 (feature list) — NOT hardcoded here until a real
    device confirms the feature index for "Color LEDs" (0x8070 family).
Until that discovery step is validated, set_frame fails closed; probe and
open are real and let the UI list the device today.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult
from pulse_hwm.rgb.drivers.raw.base_raw import RawHidDriverBase
from pulse_hwm.rgb.layout import grid_layout
from pulse_hwm.rgb.model import RgbDevice

VID = 0x046D
USAGE_PAGE = 0x0059

EXPERIMENTAL_REASON = (
    "logitech HID++ LED feature index not yet discovered (raw-protocols phase)"
)


def _load_hid():
    import hid

    return hid


class LogitechRawDriver(RawHidDriverBase):
    driver_id = "logitech_raw"
    name = "Logitech (raw HID++)"
    version = "1"
    requires_admin = False
    requires_app = ""
    vid = VID
    pid = 0xC092
    usage_page = USAGE_PAGE

    def probe(self) -> ProbeResult:
        if self._hid is None:
            self._hid = _load_hid()
        if self._hid is None:
            return ProbeResult(False, "hidapi not installed (pip install hidapi)")
        entries = self._hid.enumerate(self.vid, self.pid)
        if not any((e.get("usage_page") or 0) == USAGE_PAGE for e in entries):
            return ProbeResult(
                False, "no Logitech HID++ channel (0x0059) on known PIDs"
            )
        return ProbeResult(True)

    def open(self) -> None:
        if self._hid is None:
            raise RuntimeError("hidapi unavailable — call probe() first")
        entries = self._hid.enumerate(self.vid, self.pid)
        for entry in entries:
            if (entry.get("usage_page") or 0) == USAGE_PAGE:
                device = self._hid.device()
                device.open_path(entry["path"])
                self._device = device
                return
        raise RuntimeError("Logitech 0x0059 collection disappeared after probe")

    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(
                device_id="logitech:0",
                name="Logitech gaming keyboard",
                driver_id=self.driver_id,
                leds=120,
                layout=grid_layout(6, 20),
                modes=frozenset({"raw:first-pass"}),
            )
        ]

    def set_frame(self, device_id: str, colors: list) -> bool:
        # feature-index discovery is the next capture step; fail closed
        _ok, why = self._frame_guard(EXPERIMENTAL_REASON)
        self.last_error = why
        return False
