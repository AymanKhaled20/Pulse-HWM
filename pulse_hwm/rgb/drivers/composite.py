"""CompositeDriver — several drivers behind one RgbDriver.

Why it exists: app.py hands exactly ONE driver to the engine/worker, but
the OpenRGB pivot means TWO transports run at once — OpenRGB (everything
non-AULA) plus the native, hardware-verified AULA F75 driver. Instead of
refactoring the worker for multi-driver, the composite routes frames by
the device_id prefix each child already namespaced (e.g. "openrgb:2",
"aula_f75:0").

Contract notes:
  * probe(): available when ANY child is available; the why-not reason
    concatenates every child's reason so the UI strip stays informative.
  * devices(): the concatenation of the available children's lists.
    Children are expected to have filtered their own overlaps —
    OpenRgbDriver does that via exclude_vids.
  * set_frame(): routed by device_id prefix "<child_driver_id>:...".
  * open()/close(): fan-out to the children probe() marked available.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.model import RgbColor, RgbDevice


class CompositeDriver(RgbDriver):
    driver_id = "composite"
    name = "Composite backend"
    version = "1"

    def __init__(self, children: list[RgbDriver]) -> None:
        self._children: list[RgbDriver] = list(children)
        self._active: list[RgbDriver] = []

    def probe(self) -> ProbeResult:
        self._active = []
        reasons: list[str] = []
        for child in self._children:
            try:
                result: ProbeResult = child.probe()
            except Exception:
                reasons.append(f"{child.name}: probe crashed")
                continue
            if result.available:
                self._active.append(child)
            else:
                reason = result.reason or "unavailable"
                reasons.append(f"{child.name}: {reason}")
        if self._active:
            return ProbeResult(True)
        return ProbeResult(False, reason="; ".join(reasons))

    def open(self) -> None:
        if not self._active:
            self.probe()  # probe() before open() is the documented flow
        for child in self._active:
            child.open()

    def close(self) -> None:
        for child in self._active:
            child.close()

    def devices(self) -> list[RgbDevice]:
        result: list[RgbDevice] = []
        for child in self._active:
            result.extend(child.devices())
        return result

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        for child in self._active:
            if device_id.startswith(child.driver_id + ":"):
                return child.set_frame(device_id, colors)
        return False
