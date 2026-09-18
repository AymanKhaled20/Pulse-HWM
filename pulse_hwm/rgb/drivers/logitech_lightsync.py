"""Logitech G102/G203 LIGHTSYNC driver — native HID++ output reports.
Qt-free, no vendor software, no G HUB in the loop.

Verified LIVE on the owner's mouse (G102 LIGHTSYNC, 046D:C092):
writes go to the vendor collection (page 0xFF00, usage 0x0002) as 20-byte
output reports on report id 0x11 (rid prefix + 19-byte payload):

  enable:  11 FF 0E 50 01 03 07 00...  (software-control enable, once per open)
  static:  11 FF 0E 10 00 01 R G B 02 00 00 00 00 00 00 01 00 00 00

Frame fields learned from community research on this exact device family
(naviji/gled — MIT — credited in THIRD_PARTY.md); transport discovery and
Windows-side report-id mapping were confirmed against the device's own
report descriptor via scripts/rgb_hid_tool.py. A one-shot native
implementation, not a port of any program.

Notes:
  * earlier session data: the 0x59 LampArray collection is a Microsoft VHF
    shim that accepts writes but never reaches the physical LEDs (Windows
    Dynamic Lighting can't drive this mouse either) — that channel is NOT
    usable for direct control.
  * static mode has no brightness field; the engine scales colors instead.
  * uses 20-byte buffers exactly — the OS rejects other lengths with -1.
"""

from __future__ import annotations

import time

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.model import RgbColor, RgbDevice

VID = 0x046D
PID = 0xC092
USAGE_PAGE = 0xFF00
USAGE = 0x0002

REPORT_ID = 0x11
REPORT_LEN = 20  # rid + 19-byte payload, exact (OS rejects other sizes)

RECIPIENT_DEVICE_INDEX = 0xFF
FEATURE_INDEX = 0x0E
ENABLE_SOFT_CONTROL = (0x11, 0xFF, 0x0E, 0x50, 0x01, 0x03, 0x07)
CMD_SET_MODE = 0x10
MODE_STATIC = 0x01
MODE_STATIC_TAIL = 0x02
MODE_END_MARKER = 0x01
SETTLE_MS = 50

DEDUPE = True  # engine rewrites every tick; identical frame = no traffic


def _load_hid():
    import hid

    return hid


def build_enable_frame() -> bytes:
    """Software-control enable, sent once per open: rid 0x11 + 19 bytes with
    11 FF 0E 50 01 03 07 occupying the first seven."""
    return bytes([REPORT_ID, 0xFF, 0x0E, 0x50, 0x01, 0x03, 0x07] + [0x00] * 13)


def build_static_frame(color: RgbColor) -> bytes:
    """20-byte static-mode output frame (SetMode static + RGB)."""
    return bytes(
        [
            REPORT_ID,
            RECIPIENT_DEVICE_INDEX,
            FEATURE_INDEX,
            CMD_SET_MODE,
            0x00,
            MODE_STATIC,
            color.r,
            color.g,
            color.b,
            MODE_STATIC_TAIL,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            MODE_END_MARKER,
            0x00,
            0x00,
            0x00,
        ]
    )


def _representative(colors: list[RgbColor]) -> RgbColor | None:
    """Uniform frames stay exact; mixed remove the mean (whole-device driver)."""
    if not colors:
        return None
    first = colors[0]
    if all((c.r, c.g, c.b) == (first.r, first.g, first.b) for c in colors):
        return first
    count = len(colors)
    return RgbColor(
        sum(c.r for c in colors) // count,
        sum(c.g for c in colors) // count,
        sum(c.b for c in colors) // count,
    )


class LogitechLightsyncDriver(RgbDriver):
    driver_id = "logitech_lightsync"
    name = "Logitech G102/G203 LIGHTSYNC"
    version = "1"
    requires_admin = False

    def __init__(self, hid_module=None):
        self._hid = hid_module
        self._device = None
        self._last_color: RgbColor | None = None
        self.last_error: str = ""

    # ── probe / open / close ────────────────────────────────────────────
    def probe(self) -> ProbeResult:
        if self._hid is None:
            self._hid = _load_hid()
        if self._hid is None:
            return ProbeResult(False, "hidapi not installed (pip install hidapi)")
        for entry in self._hid.enumerate(VID, PID):
            if (entry.get("usage_page") or 0) == USAGE_PAGE and (
                entry.get("usage") or 0
            ) == USAGE:
                return ProbeResult(True)
        return ProbeResult(
            False, "no G102/G203 LIGHTSYNC mouse plugged in (no LED channel)"
        )

    def open(self) -> None:
        if self._hid is None:
            raise RuntimeError("hidapi unavailable — call probe() first")
        for entry in self._hid.enumerate(VID, PID):
            if (entry.get("usage_page") or 0) == USAGE_PAGE and (
                entry.get("usage") or 0
            ) == USAGE:
                device = self._hid.device()
                device.open_path(entry["path"])
                self._device = device
                try:
                    device.write(build_enable_frame())
                except Exception as exc:
                    self.last_error = f"enable frame rejected: {exc}"
                time.sleep(SETTLE_MS / 1000.0)  # device needs the brief set-up
                return
        raise RuntimeError("LIGHTSYNC collection disappeared after probe")

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        self._last_color = None

    # ── devices / frames ────────────────────────────────────────────────
    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(
                device_id="logitech_g102:0",
                name="G102/G203 LIGHTSYNC mouse",
                driver_id=self.driver_id,
                leds=1,  # whole-mouse zone
                modes=frozenset({"static"}),
            )
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        del device_id
        if self._device is None:
            self.last_error = "not open"
            return False
        color = _representative(colors)
        if color is None:
            return False
        if DEDUPE and color == self._last_color:
            return True
        try:
            wrote = self._device.write(build_static_frame(color))
        except Exception as exc:
            self.last_error = f"write failed: {exc}"
            return False
        if wrote != REPORT_LEN:
            self.last_error = f"frame write returned {wrote} (expected {REPORT_LEN})"
            return False
        self._last_color = color
        return True

    def set_brightness(self, device_id: str, pct: int) -> bool:
        # static mode carries no brightness field; engine scales colors
        del device_id, pct
        return False
