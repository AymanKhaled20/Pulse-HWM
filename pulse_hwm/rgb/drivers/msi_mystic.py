"""MSI Mystic Light driver — MysticLight_SDK.dll via ctypes.

Backend: MSI Center's Mystic Light SDK module (not redistributed — see
THIRD_PARTY.md). Important SDK constraint honored here: Mystic Light's
color API is a fixed 16-slot palette (`MSC_SetLedColor` takes a color
INDEX, not RGB), so set_frame picks the NEAREST palette slot for the
frame's representative color. Fidelity is capped at those 16 colors —
that is the SDK's limit, not a Pulse decision.

DLL lookup: MSI Center ships the DLL under its own install tree; probing
history/opens reads the presence and Init() success. Injection of the
ctypes loader keeps offline tests.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.model import RgbColor, RgbDevice

DLL_NAME = "MysticLight_SDK.dll"
_DLL_HINTS = (
    Path(r"C:\Program Files (x86)\MSI\One Dragon Center"),
    Path(r"C:\Program Files (x86)\MSI\MSI Center"),
    Path(r"C:\Program Files\MSI\MSI Center"),
)

# Mystic Light's 16 palette slots (the SDK's documented color indices 0..15)
MSI_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # 0 BLACK / off
    (0, 0, 255),  # 1 BLUE
    (0, 255, 255),  # 2 CYAN
    (0, 255, 0),  # 3 GREEN
    (255, 255, 0),  # 4 YELLOW
    (255, 128, 0),  # 5 ORANGE
    (255, 0, 0),  # 6 RED
    (255, 0, 255),  # 7 MAGENTA
    (255, 80, 160),  # 8 PINK
    (128, 0, 255),  # 9 PURPLE
    (255, 255, 255),  # 10 WHITE
    (128, 255, 0),  # 11 CHARTREUSE
    (0, 128, 255),  # 12 SKY
    (128, 128, 128),  # 13 GRAY
    (90, 50, 20),  # 14 BROWN
    (30, 144, 255),  # 15 DODGER
)


def _find_dll() -> Path | None:
    for path in _DLL_HINTS:
        candidate = path / DLL_NAME
        if candidate.exists():
            return candidate
    return None


def nearest_palette_index(color: RgbColor) -> int:
    """Euclidean RGB distance to the 16-slot palette (the SDK's truth)."""
    best_index, best_distance = 0, float("inf")
    for index, (r, g, b) in enumerate(MSI_PALETTE):
        distance = (color.r - r) ** 2 + (color.g - g) ** 2 + (color.b - b) ** 2
        if distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


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


class MysticLightDriver(RgbDriver):
    driver_id = "msi_mystic"
    name = "MSI Mystic Light"
    version = "1"
    requires_admin = False
    requires_app = "MSI Center + Mystic Light SDK module"

    def __init__(self, loader=None, dll_path=None):
        self._loader = loader
        self._dll_path = dll_path
        self._dll = None
        self._last_index: int | None = None
        self.last_error: str = ""

    # ── probe / open / close ────────────────────────────────────────────
    def probe(self) -> ProbeResult:
        path = self._dll_path
        if path is None:
            path = _find_dll()
        if path is None or not Path(path).exists():
            return ProbeResult(
                False,
                f"{DLL_NAME} not found — install MSI Center with the Mystic "
                "Light SDK module",
                needs_install="MSI Center",
            )
        return ProbeResult(True)

    def open(self) -> None:
        result = self.probe()
        if not result.available:
            raise RuntimeError(result.reason)
        path = Path(self._dll_path or _find_dll())
        self._dll = (self._loader or ctypes.CDLL)(str(path))
        try:
            self._dll.MSC_MysticLight_Initialize.restype = ctypes.c_int
        except AttributeError:
            pass
        rc = self._dll.MSC_MysticLight_Initialize()
        if rc != 0:
            self.last_error = f"SDK initialize rc={rc} (is MSI Center running?)"
            raise RuntimeError(self.last_error)

    def close(self) -> None:
        if self._dll is not None:
            try:
                self._dll.MSC_MysticLight_SetLedColor.restype = ctypes.c_int
            except AttributeError:
                pass
            try:
                self._dll.MSC_MysticLight_Disconnect()
            except Exception:
                pass
        self._dll = None

    # ── devices / frames ────────────────────────────────────────────────
    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(
                device_id="msi:all",
                name="MSI devices (all)",
                driver_id=self.driver_id,
                leds=1,
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
        palette_index = nearest_palette_index(representative)
        if palette_index == self._last_index:
            return True  # SDK holds its own lighting; dedupe
        try:
            # declare argtypes once: plain ints convert cleanly, no c_int
            # wrappers reaching the SDK (also stable under fakes)
            try:
                self._dll.MSC_SetLedColor.argtypes = [
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                ]
            except AttributeError:
                pass
            ok = bool(
                self._dll.MSC_SetLedColor(
                    -1,  # ALL devices
                    -1,  # ALL zones/LEDs
                    palette_index,
                )
            )
        except Exception as exc:
            self.last_error = f"SetLedColor failed: {exc}"
            return False
        if not ok:
            self.last_error = "MSC_SetLedColor returned false"
            return False
        self._last_index = palette_index
        return True

    def set_brightness(self, device_id: str, pct: int) -> bool:
        del device_id, pct
        return False
