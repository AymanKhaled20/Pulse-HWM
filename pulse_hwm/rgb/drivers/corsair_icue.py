"""Corsair iCUE driver — official 'cuesdk' ctypes binding (optional dep).

Backend: the cuesdk package (Corsair's own ctypes binding, pip install
cuesdk) talking to iCUE 4.x. Not distributed with Pulse — see
THIRD_PARTY.md; probe reports it missing with an install hint. Verifying
against iCUE 5 needs the owner's machine (G HUB/iCUE/Synapse phase).

Scope: broadcast static color to ALL devices (ExclusiveLightingControl not
claimed — coexists with iCUE; frame representative color only, since the
cue SDK broadcast API is whole-setup).
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.model import RgbColor, RgbDevice


def _load_cuesdk():
    try:
        from cuesdk import CueSdk  # type: ignore

        return CueSdk
    except Exception:
        return None


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


class CorsairDriver(RgbDriver):
    driver_id = "corsair_icue"
    name = "Corsair iCUE"
    version = "1"
    requires_admin = False
    requires_app = "Corsair iCUE"

    def __init__(self, sdk_factory=None):
        self._sdk_factory = sdk_factory
        self._sdk = None
        self._last_color: RgbColor | None = None
        self.last_error: str = ""

    # ── probe / open / close ────────────────────────────────────────────
    def probe(self) -> ProbeResult:
        if self._sdk_factory is None:
            factory = _load_cuesdk()
        else:
            factory = self._sdk_factory
        if factory is None:
            return ProbeResult(
                False,
                "cuesdk package missing (pip install cuesdk) or iCUE absent",
                needs_install="Corsair iCUE",
            )
        return ProbeResult(True)

    def open(self) -> None:
        factory = self._sdk_factory or _load_cuesdk()
        if factory is None:
            raise RuntimeError("cuesdk unavailable — probe() first")
        self._sdk = factory()
        connect = getattr(self._sdk, "connect", None)
        if callable(connect):
            connect()  # iCUE handshake; errors surface via set_frame below

    def close(self) -> None:
        if self._sdk is not None:
            shutdown = getattr(self._sdk, "shutdown", None) or getattr(
                self._sdk, "disconnect", None
            )
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    pass
        self._sdk = None

    # ── devices / frames ────────────────────────────────────────────────
    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(
                device_id="corsair:all",
                name="Corsair devices (all)",
                driver_id=self.driver_id,
                leds=1,
                modes=frozenset({"static"}),
            )
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        del device_id
        if self._sdk is None:
            self.last_error = "not open"
            return False
        representative = _representative(colors)
        if representative is None:
            return False
        if representative == self._last_color:
            return True  # iCUE holds last applied lighting
        sdk_broadcast = getattr(self._sdk, "broadcast_colors", None)
        if sdk_broadcast is None:
            self.last_error = "cuesdk API missing broadcast_colors"
            return False
        try:
            ok = bool(sdk_broadcast(representative))
        except Exception as exc:
            self.last_error = f"broadcast failed: {exc}"
            return False
        if not ok:
            self.last_error = "iCUE refused the color"
            return False
        self._last_color = representative
        return True

    def set_brightness(self, device_id: str, pct: int) -> bool:
        del device_id, pct
        return False
