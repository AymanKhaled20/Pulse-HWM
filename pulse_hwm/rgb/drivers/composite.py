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
        # device_id → child. Populated at every devices() call; set_frame
        # consults it FIRST and only then falls back to the driver_id
        # prefix. Children may name devices differently from their
        # driver_id (the AULA driver is "aula_f75" but its device id is
        # "aula:0"), so prefix matching alone is NOT sufficient.
        self._route: dict[str, RgbDriver] = {}
        self.last_error: str = ""

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
        self._route = {}
        for child in self._active:
            child_devices = child.devices()
            for device in child_devices:
                self._route[device.device_id] = child
            result.extend(child_devices)
        return result

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        self.last_error = ""
        child = self._route.get(device_id)
        if child is None and device_id != "":
            # unknown id: maybe the child enumerated after we last build the
            # route — retry against the driver_id prefix (openrgb:N etc.)
            for candidate in self._active:
                if device_id.startswith(candidate.driver_id + ":"):
                    child = candidate
                    break
        if child is None:
            self.last_error = "no child owns device"
            return False
        accepted = child.set_frame(device_id, colors)
        if not accepted:
            detail = str(getattr(child, "last_error", "") or "frame rejected")
            self.last_error = f"{child.name}: {detail}"
        return accepted
