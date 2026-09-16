"""MSI RAW driver — HONESTLY a detection stub (see base_raw phase notes).

Why no write path: MSI Mystic Light on desktop motherboards is driven
through the vendor SDK/kernel service; there is no community-validated
vendor-free HID write surface (the 0x1462 HID interface seen on the scan
machine answers enumeration but the lighting commands are not public).
OpenRGB solves this the same way — with a kernel driver or the SDK.

This stub makes the ecosystem VISIBLE in the RGB tab (status panel lists
MSI hardware with an honest reason) without pretending to control it.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult
from pulse_hwm.rgb.drivers.raw.base_raw import RawHidDriverBase
from pulse_hwm.rgb.model import RgbDevice

VID = 0x1462
NO_SURFACE_REASON = (
    "MSI Mystic Light has no public vendor-free write path "
    "(kernel driver required) — detection-only"
)


class MysticLightRawDriver(RawHidDriverBase):
    driver_id = "msi_raw"
    name = "MSI (raw — detect only)"
    version = "1"
    requires_admin = False
    requires_app = "MSI Center (or a kernel driver)"
    vid = VID
    pid = 0  # every MSI VID device

    def probe(self) -> ProbeResult:
        if self._hid is None:
            import hid

            self._hid = hid
        if self._hid is None:
            return ProbeResult(False, "hidapi not installed (pip install hidapi)")
        entries = self._hid.enumerate(self.vid, self.pid)
        if not entries:
            return ProbeResult(False, "no MSI device detected")
        return ProbeResult(False, NO_SURFACE_REASON)

    def open(self) -> None:
        # never opens: probe() never reports available, so the registry
        # never hands this driver to the engine
        raise RuntimeError(NO_SURFACE_REASON)

    def devices(self) -> list[RgbDevice]:
        return []

    def set_frame(self, device_id: str, colors: list) -> bool:
        del device_id, colors
        self.last_error = NO_SURFACE_REASON
        return False
