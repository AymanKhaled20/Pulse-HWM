"""AULA F75 driver — hidapi transport over the SinoWealth protocol.

Phase 6 supplied the pure codec; this file is the ONLY place raw HID I/O
happens for Aula. The `hid` module (hidapi) is dependency-injected so every
I/O behavior is testable with a fake and the real import stays a boot-time
concern.

Hardware strategy (and why dedupe matters):
  * The firmware effect-change sequence ends with SAVE — a flash commit.
    The engine re-asserts frames at rgb_engine_fps, so replaying the whole
    4-phase sequence every frame would hammer the keyboard's flash. The
    driver dedupes by representative color and transmits only on change.
  * set_frame() transmits a REPRESENTATIVE color (uniform frame → the exact
    color; mixed frame → average). True per-key display needs the F75
    key→LED-index calibration (a one-time capture on real hardware, tracked
    in docs/RGB.md) — wired mode first, dongle later.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers import aula_protocol as ap
from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.layout import aula_f75_layout
from pulse_hwm.rgb.model import RgbColor, RgbDevice

VID = 0x258A
PID_WIRED = 0x010C
PID_DONGLE = 0x010D  # 2.4 GHz variant — phase 25 follow-up, wired first

# SinoWealth vendor collections live in usage pages 0xFF00-0xFFFF
VENDOR_USAGE_PAGE_MIN = 0xFF00
VENDOR_USAGE_PAGE_MAX = 0xFFFF
READ_ECHO_TIMEOUT_MS = 120


def _load_hid():
    try:
        import hid

        return hid
    except Exception:
        return None


def _representative(colors: list[RgbColor]) -> tuple[int, int, int] | None:
    """Single decision color for the whole frame. Uniform frames keep their
    exact color; mixed frames average (per-key precision needs the index
    calibration, so averaging loses nothing today but is honest)."""
    if not colors:
        return None
    if len({(c.r, c.g, c.b) for c in colors}) == 1:
        return (colors[0].r, colors[0].g, colors[0].b)
    count = len(colors)
    average = sum(c.r for c in colors) // count
    average_g = sum(c.g for c in colors) // count
    average_b = sum(c.b for c in colors) // count
    return (average, average_g, average_b)


class AulaDriver(RgbDriver):
    driver_id = "aula_f75"
    name = "AULA F75"
    version = "1"
    requires_admin = False

    def __init__(self, hid_module=None, echo_read_ms: int = READ_ECHO_TIMEOUT_MS):
        self._hid = hid_module
        self._echo_read_ms = echo_read_ms
        self._device = None  # hidapi handle once open() succeeds
        self._last_color: tuple[int, int, int] | None = None
        self._config_source: list[list[int]] | None = None
        self.last_error: str = ""

    # ── probe / open / close ────────────────────────────────────────────
    def probe(self) -> ProbeResult:
        if self._hid is None:
            try:
                import hid

                self._hid = hid
            except Exception:
                return ProbeResult(False, "hidapi not installed (pip install hidapi)")
        try:
            entries = self._hid.enumerate(VID, PID_WIRED)
        except Exception as exc:
            return ProbeResult(False, f"hid enumeration failed: {exc}")
        if not entries:
            return ProbeResult(False, "no AULA F75 keyboard detected (wired)")
        return ProbeResult(True)

    def open(self) -> None:
        if self._hid is None:
            raise RuntimeError("hidapi unavailable — probe() first")
        entries = self._hid.enumerate(VID, PID_WIRED)
        if not entries:
            raise RuntimeError("AULA F75 disappeared between probe and open")
        self._device = self._hid.device()  # hidapi: open happens in-place
        self._device.open_path(self._pick_entry(entries)["path"])
        # the vendor config read is the read-modify-write source
        config = self._read_config()
        if config is not None:
            self._config_source = config["fragments"]

    def _pick_entry(self, entries: list) -> dict:
        for entry in entries:
            usage = int(entry.get("usage_page", 0) or 0)
            if VENDOR_USAGE_PAGE_MIN <= usage <= VENDOR_USAGE_PAGE_MAX:
                return entry
        return entries[0]

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        self._last_color = None

    # ── devices / frames / brightness ──────────────────────────────────
    def devices(self) -> list[RgbDevice]:
        layout = aula_f75_layout()
        return [
            RgbDevice(
                device_id="aula:0",
                name="AULA F75",
                driver_id=self.driver_id,
                leds=len(layout.positions),
                layout=layout,
            )
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        del device_id  # the F75 is one device ("aula:0")
        if self._device is None:
            self.last_error = "not open"
            return False
        representative = _representative(colors)
        if representative is None:
            return False
        if representative == self._last_color:
            return True  # identical frame: no flash-wear, still succeeded
        if self._apply_color_effect(representative):
            self._last_color = representative
            return True
        return False

    def set_brightness(self, device_id: str, pct: int) -> bool:
        config = self._effective_config()
        if config is None or self._device is None:
            return False
        fragments = ap.set_effect_on_config(
            config,
            effect=1,
            color_mode=ap.COLOR_MODE_CUSTOM,
            effect_brightness=ap.brightness_level(pct),
        )
        return self._send_config(fragments)

    # ── the 4-phase sequence ────────────────────────────────────────────
    def _effective_config(self) -> list | None:
        if self._config_source is not None:
            return self._config_source
        config = self._read_config()
        if config is not None:
            self._config_source = config["fragments"]
        return self._config_source

    def _read_config(self) -> dict | None:
        """Phase 1: READ request + 10 response fragments. Returns None on
        any timeout/validation failure (callers degrade, never raise)."""
        try:
            if not self._send_fragment(ap.read_request()):
                return None
            response = []
            for seq in range(ap.CONFIG_FRAGMENTS):
                fragment = self._read_fragment()
                if fragment is None:
                    self.last_error = "config read timed out"
                    return None
                response.append(fragment)
            parsed = ap.parse_config_response(response)
            if parsed is None:
                self.last_error = "config response malformed"
            return parsed
        except Exception as exc:
            self.last_error = f"config read failed: {exc}"
            return None

    def _apply_color_effect(self, color: tuple[int, int, int]) -> bool:
        """read → write(effect 1 Fixed_on, custom color mode) → palette → save."""
        config = self._effective_config()
        if config is None:
            return False
        write_fragments = ap.set_effect_on_config(
            config,
            effect=1,  # Fixed_on — the uniform-color effect
            color_mode=ap.COLOR_MODE_CUSTOM,
        )
        if not self._send_config(write_fragments):
            return False
        for fragment in ap.palette_fragments(RgbColor(*color)):
            if not self._send_fragment(fragment):
                self.last_error = "palette write failed"
                return False
        if not self._send_fragment(ap.save_request()):
            self.last_error = "save failed"
            return False
        return True

    def _send_config(self, fragments: list[list[int]]) -> bool:
        for fragment in fragments:
            if not self._send_fragment(fragment):
                self.last_error = "config write failed"
                return False
        return True

    # ── raw I/O helpers ─────────────────────────────────────────────────
    def _send_fragment(self, fragment: list[int]) -> bool:
        """Write one fragment, then wait for the keyboard's echo (the spec
        requires it before the next fragment). Best-effort: a missing echo
        still counts as sent unless the FIRST echo read is empty."""
        try:
            if self._device.write(bytes(bytearray(fragment))) < 1:
                self.last_error = "write returned 0 bytes"
                return False
            # drain the echo best-effort (strictness would stall the tick on
            # chatty firmware; the echo is an optimization, not a gate)
            self._device.read(ap.FRAGMENT_SIZE, self._echo_read_ms)
            return True
        except Exception as exc:
            self.last_error = f"raw write failed: {exc}"
            return False

    def _read_fragment(self) -> list[int] | None:
        """Blocking read of the next 20-byte response fragment."""
        try:
            for _ in range(4):  # realloc frames (ordinary key input) first
                data = self._device.read(ap.FRAGMENT_SIZE, self._echo_read_ms)
                if len(data) == ap.FRAGMENT_SIZE and data[0] == ap.REPORT_ID:
                    return list(data)
                if len(data) == ap.FRAGMENT_SIZE:
                    continue  # exact size but odd report id — drain it
            return None
        except Exception as exc:
            self.last_error = f"raw read failed: {exc}"
            return None
