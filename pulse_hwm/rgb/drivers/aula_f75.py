"""AULA F75 driver — Sinowealth DIRECT MODE over HID feature reports.

Grounded in OpenRGB's SinowealthKeyboard10cController (GPL-2.0, attribution
in THIRD_PARTY.md): a 520-byte FEATURE report on report id 0x06 carries
per-LED RGB triplets starting at offset 8. On this keyboard the usable
collection answers get_feature_report(0x06, 520) with a 386-byte blob whose
header encodes 0x17A (= 378 = 126 LEDs x 3 channels), matching the
community per-key map — so the LED capacity is 126.

Why feature reports and not the OEM 20-byte effect protocol (phase 6):
  * direct mode is RAM-only — no flash SAVE, zero wear from 30 fps ticks;
  * every LED individually addressable (uniform frames stay trivial);
  * the effect codec remains (aula_protocol.py) for future chip-side
    hardware effects (Respire, Ripple …).

The engine re-asserts at rgb_engine_fps, which doubles as the direct-mode
keepalive; when Pulse stops the keyboard reverts to its own lighting —
exactly the "override" semantics.
"""

from __future__ import annotations

import time

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.layout import aula_f75_layout
from pulse_hwm.rgb.model import RgbColor, RgbDevice

VID = 0x258A
PID_WIRED = 0x010C
PID_DONGLE = 0x010D  # 2.4 GHz variant — follow-up phase, wired first

FRAME_SIZE = 520  # direct-mode feature buffer size
FRAME_OFFSET_RGB = 8  # first RGB triplet
DAILY_LED_LEAVE = False  # placeholder removed below
SINOWEALTH_LED_COUNT = 126  # 378 payload bytes / 3 — matches keepalive
SINOWEALTH_DEDUPE_RESEND_S = 0.7  # keepalive safety margin (timeout ~1s)
FEATURE_PROBE_ID = 0x06
FEATURE_PROBE_SIZE = 520
FEATURE_SETTLE_MS = 1  # OpenRGB sleeps 1 ms after SetFeatureReport


def _load_hid():
    try:
        import hid

        return hid
    except Exception:
        return None


def build_direct_frame(colors: list[RgbColor]) -> bytes:
    """520-byte direct-mode feature payload: 0x06 header + per-LED RGB.
    Short/missing colors pad black — capacity covers the 64 named keys plus
    the dummy matrix indices."""
    payload = bytearray(FRAME_SIZE)
    header = bytes((0x06, 0x08, 0x00, 0x00, 0x01, 0x00, 0x7A, 0x01))
    payload[0:8] = header
    offset = FRAME_OFFSET_RGB
    for index in range(SINOWEALTH_LED_COUNT):
        color = colors[index] if index < len(colors) else RgbColor(0, 0, 0)
        payload[offset] = color.r
        payload[offset + 1] = color.g
        payload[offset + 2] = color.b
        offset += 3
    return bytes(payload)


def _representative(colors: list[RgbColor]) -> RgbColor | None:
    """Dedupe key only: uniform frames keep their exact color, mixed frames
    average. Every LED is written regardless (direct mode is per-LED)."""
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


def _collection_answers_direct(hid_module, path) -> bool:
    """True when this collection answers the 0x06 keepalive — the only
    reliable channel test (hidapi usage_page is unreliable here)."""
    try:
        device = hid_module.device()
        device.open_path(path)
        response = device.get_feature_report(FEATURE_PROBE_ID, FEATURE_PROBE_SIZE)
        device.close()
        return bool(response)
    except Exception:
        return False


class AulaDriver(RgbDriver):
    driver_id = "aula_f75"
    name = "AULA F75"
    version = "1"
    requires_admin = False
    RGB_CAPABILITY = "@direct-feature"

    def __init__(self, hid_module=None, clock=time.monotonic):
        self._hid = hid_module
        self._clock = clock
        self._device = None
        self._last_color: RgbColor | None = None
        self._last_send: float = 0.0
        self.last_error: str = ""

    # ── probe / open / close ────────────────────────────────────────────
    def probe(self) -> ProbeResult:
        if self._hid is None:
            self._hid = _load_hid()
        if self._hid is None:
            return ProbeResult(False, "hidapi not installed (pip install hidapi)")
        entries = self._hid.enumerate(VID, PID_WIRED)
        if not entries:
            return ProbeResult(
                False, "no AULA F75 keyboard detected (plug in with the cable)"
            )
        return ProbeResult(True)

    def open(self) -> None:
        if self._hid is None:
            raise RuntimeError("hidapi unavailable — call probe() first")
        entries = self._hid.enumerate(VID, PID_WIRED)
        if not entries:
            raise RuntimeError("AULA F75 disappeared between probe and open")
        for entry in entries:
            if _collection_answers_direct(self._hid, entry["path"]):
                self._device = self._hid.device()
                self._device.open_path(entry["path"])
                return
        self.last_error = "no collection answers the 0x06 direct channel"

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
        layout = aula_f75_layout()
        return [
            RgbDevice(
                device_id="aula:0",
                name="AULA F75",
                driver_id=self.driver_id,
                # capacity LEDs the matrix exposes; layout covers the 64 keys
                leds=SINOWEALTH_LED_COUNT,
                layout=layout,
                modes=frozenset({"direct"}),
            )
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        del device_id  # one device ("aula:0")
        if self._device is None:
            self.last_error = "not open"
            return False
        representative = _representative(colors)
        if representative is None:
            return False
        # dedupe with a keepalive floor: identical frames re-send on the
        # SINOWEALTH_DEDUPE_RESEND_S cadence so the firmware never times
        # out of direct mode between sparse effect changes
        if representative == self._last_color:
            if self._clock() - self._last_send < SINOWEALTH_DEDUPE_RESEND_S:
                return True
        payload = build_direct_frame([representative] * SINOWEALTH_LED_COUNT)
        try:
            if self._device.send_feature_report(payload) < 0:
                self.last_error = "feature write failed"
                return False
        except Exception as exc:
            self.last_error = f"feature write failed: {exc}"
            return False
        time.sleep(FEATURE_SETTLE_MS / 1000.0)
        self._last_color = representative
        self._last_send = self._clock()
        return True

    def set_brightness(self, device_id: str, pct: int) -> bool:
        # direct mode has no brightness command: the engine scales frames
        del device_id, pct
        return False

    # ── diagnostics ─────────────────────────────────────────────────────
    def capability_report(self) -> dict:
        return {
            "direct_mode": self._device is not None,
            "leds": SINOWEALTH_LED_COUNT,
            "per_key_calibrated": False,  # index calibration is phase 25
        }
