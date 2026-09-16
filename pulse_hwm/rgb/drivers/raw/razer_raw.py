"""Razer RAW driver — vendor-defined collection, OpenRGB-style matrix.

UNVERIFIED FIRST PASS (see module docstring in base_raw.py):
  * probe/open: VID 0x1532 + usage page 0xFFA0 (confirmed reachable on the
    owner's device — scripts/rgb_raw_scan.py).
  * frame writes: openrazer-shaped matrix envelopes (razer_protocol.py);
    only run when rgb_allow_raw_protocols is on; otherwise fail closed.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult
from pulse_hwm.rgb.drivers.raw.base_raw import RawHidDriverBase, _uniform
from pulse_hwm.rgb.drivers.raw.razer_protocol import (
    ENVELOPE_LEN,
    matrix_row_payloads,
)
from pulse_hwm.rgb.layout import grid_layout
from pulse_hwm.rgb.model import RgbColor, RgbDevice

VID = 0x1532
USAGE_PAGE = 0xFFA0

EXPERIMENTAL_REASON = "razer matrix protocol not validated yet (raw-protocols phase)"


def _load_hid():
    import hid

    return hid


class RazerRawDriver(RawHidDriverBase):
    driver_id = "razer_raw"
    name = "Razer (raw HID)"
    version = "1"
    requires_admin = False
    requires_app = ""

    def __init__(self, hid_module=None, pids=(0x0537,), rows=22, row_size=6):
        super().__init__(hid_module)
        self.pids = tuple(pids)
        self.rows = rows
        self.row_size = row_size

    def probe(self) -> ProbeResult:
        if self._hid is None:
            self._hid = _load_hid()
        if self._hid is None:
            return ProbeResult(False, "hidapi not installed (pip install hidapi)")
        for pid in self.pids:
            entries = self._hid.enumerate(VID, pid)
            if any((e.get("usage_page") or 0) == USAGE_PAGE for e in entries):
                return ProbeResult(True)
        return ProbeResult(
            False, "no Razer device on the 0xFFA0 control channel (plug it in)"
        )

    def open(self) -> None:
        if self._hid is None:
            raise RuntimeError("hidapi unavailable — call probe() first")
        for pid in self.pids:
            for entry in self._hid.enumerate(VID, pid):
                if (entry.get("usage_page") or 0) == USAGE_PAGE:
                    device = self._hid.device()
                    device.open_path(entry["path"])
                    self._device = device
                    return
        raise RuntimeError("Razer 0xFFA0 collection disappeared after probe")

    def close(self) -> None:
        RawHidDriverBase.close(self)

    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(
                device_id="razer:0",
                name="Razer keyboard",
                driver_id=self.driver_id,
                leds=self.rows * self.row_size,
                layout=grid_layout(self.rows, self.row_size),
                modes=frozenset({"raw:first-pass"}),
            )
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        del device_id
        ok, why = self._frame_guard(EXPERIMENTAL_REASON)
        if not ok:
            self.last_error = why
            return False
        color = _uniform(colors)
        if color is None:
            return False
        try:
            for payload in matrix_row_payloads(color, self.rows, self.row_size):
                if len(payload) != ENVELOPE_LEN:
                    self.last_error = "internal frame size bug"
                    return False
                self._device.send_feature_report(payload)
        except Exception as exc:
            self.last_error = f"razer raw write failed: {exc}"
            return False
        return True
