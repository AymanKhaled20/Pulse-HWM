"""Logitech G driver — Logitech LED Illumination SDK (ctypes).

Backend: LogitechLED.dll shipped with LGS / Logitech G HUB ("Compatibility
Mode" in G HUB). The driver loads it by argument-injected loader so tests
run a fake and the real DLL lookup is boot-only. Not redistributed — see
THIRD_PARTY.md; `probe` reports the missing-app reason and install hint.

SDK notes (LED Illumination, the LogiLed* surface):
  * colors are integer PERCENTAGES 0-100 per channel — the driver converts
    our 0-255 bytes with round(x * 100 / 255) (loss ≤ 1/255);
  * global lighting only (zero per-key surface in this SDK version), so a
    frame's representative color is written behind LogiLedSetLighting;
  * save_targeting (LogiLedSaveCurrentLightingForMousepad …) is unused;
  * Init/Shutdown bookkeeping in open()/close() mirrors the OEM contract.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.model import RgbColor, RgbDevice

DLL_NAME = "LogitechLED.dll"
_GHUB_LEGACY = Path(r"C:\Program Files\LGHUB\sdks") / "sdk_legacy_led_x64.dll"
_DLL_HINTS = (
    Path(r"C:\Program Files\Logitech Gaming Software\SDK\LED\x64"),
    Path(r"C:\Program Files\LGHUB\sdk"),
)


def _find_dll() -> Path | None:
    # G HUB (2026) vendors the legacy LED SDK under sdks\ with a renamed
    # DLL — no separate LGS install needed when GHUB is present
    if _GHUB_LEGACY.exists():
        return _GHUB_LEGACY
    for path in _DLL_HINTS:
        candidate = path / DLL_NAME
        if candidate.exists():
            return candidate
    return None


def _load_ctypes(dll_path: Path):
    return ctypes.CDLL(str(dll_path))


def _representative(colors: list[RgbColor]) -> RgbColor | None:
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


class LogitechDriver(RgbDriver):
    driver_id = "logitech"
    name = "Logitech G"
    version = "1"
    requires_admin = False
    requires_app = "Logitech G HUB (+ Enable Integerated / LGS with LED SDK)"

    def __init__(self, loader=_load_ctypes, dll_path=None):
        self._loader = loader
        self._dll_path = dll_path
        self._dll = None
        self._last_color: RgbColor | None = None
        self.last_error: str = ""

    # ── probe / open / close ────────────────────────────────────────────
    def probe(self) -> ProbeResult:
        path = self._dll_path
        if path is None:
            path = _find_dll()
        if path is None or not Path(path).exists():
            return ProbeResult(
                False,
                "LogitechLED.dll not found — install Logitech G HUB, enable "
                "its SDK, or LGS",
                needs_install="Logitech G HUB",
            )
        return ProbeResult(True)

    def open(self) -> None:
        result = self.probe()
        if not result.available:
            raise RuntimeError(result.reason)
        path = self._dll_path or _find_dll()
        self._dll = self._loader(path)
        self._dll.LogiLedInit()
        try:  # ctypes-only decoration; fakes may not allow it
            self._dll.LogiLedSetTargetDevice.argtypes = [ctypes.c_int]
        except AttributeError:
            pass
        ok = self._dll.LogiLedSetTargetDevice(0x07)  # keyboards + mice + pads
        if not ok:
            self.last_error = "SetTargetDevice failed (is G HUB running?)"
            raise RuntimeError(self.last_error)

    def close(self) -> None:
        if self._dll is not None:
            try:
                self._dll.LogiLedShutdown()
            except Exception:
                pass
        self._dll = None

    # ── devices / frames ────────────────────────────────────────────────
    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(
                device_id="logitech:all",
                name="Logitech devices (all)",
                driver_id=self.driver_id,
                leds=1,  # whole-device control via the SDK
                modes=frozenset({"static"}),
            )
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        del device_id
        if self._dll is None:
            self.last_error = "not open"
            return False
        representative = _representative(colors)
        if representative is None:
            return False
        if representative == self._last_color:
            return True  # SDK re-asserts its own lighting; dedupe avoids traffic
        try:
            ok = bool(
                self._dll.LogiLedSetLighting(
                    min(100, representative.r * 100 // 255),
                    min(100, representative.g * 100 // 255),
                    min(100, representative.b * 100 // 255),
                )
            )
        except Exception as exc:
            self.last_error = f"SetLighting failed: {exc}"
            return False
        if not ok:
            self.last_error = "LogiLedSetLighting returned false"
            return False
        self._last_color = representative
        return True

    def set_brightness(self, device_id: str, pct: int) -> bool:
        # the LED SDK has no brightness control: frames already carry scale
        del device_id, pct
        return False
