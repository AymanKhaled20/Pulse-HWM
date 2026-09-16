"""First-pass raw HID driver plumbing (vendor-free phase). See the phase
design in raw/__init__.py for why set_frame fails closed behind a gate."""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.model import RgbColor


class RawHidDriverBase(RgbDriver):
    """Shared plumbing: find the RGB control collection by
    (vid, pid, usage_page), open it, surface one RgbDevice.

    Subclasses usually declare vid/pid/usage_page and implement
    devices()/set_frame(); multi-PID vendors override _find_entry as well.
    """

    vid: int = 0
    pid: int = 0
    usage_page: int = 0

    def __init__(self, hid_module=None):
        self._hid = hid_module
        self._device = None
        self._path: str = ""
        self.last_error: str = ""
        self.experimental_allowed = False  # app wires this from settings

    # ── enumeration helpers ─────────────────────────────────────────────
    def _find_entry(self) -> dict | None:
        if self._hid is None:
            import hid

            self._hid = hid
        for entry in self._hid.enumerate(self.vid, self.pid):
            if (entry.get("usage_page") or 0) == self.usage_page:
                return entry
        return None

    # ── shared frame guard ──────────────────────────────────────────────
    def _frame_guard(self, experiment_reason: str) -> tuple[bool, str]:
        """(ok, error): blocks protocol writes behind the experimental
        gate and requires a live handle — fail closed on both."""
        if self._device is None:
            return False, "not open"
        if not self.experimental_allowed:
            return False, experiment_reason
        return True, ""

    def set_brightness(self, device_id: str, pct: int) -> bool:
        del device_id, pct
        return False  # engine scales colors; raw protocols lack brightness


def _uniform(colors: list[RgbColor]) -> RgbColor | None:
    """Uniform frames stay exact; mixed frames degrade to the mean until
    per-key index calibration lands per device family."""
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


def probe_from_entry(hid_module, vid, pid, usage_page) -> ProbeResult:
    """Common probe: hidapi present? then the collection present?"""
    if hid_module is None:
        import hid

        hid_module = hid
    if hid_module is None:
        return ProbeResult(False, "hidapi not installed (pip install hidapi)")
    entries = hid_module.enumerate(vid, pid)
    for entry in entries:
        if (entry.get("usage_page") or 0) == usage_page:
            return ProbeResult(True)
    return ProbeResult(False, f"no RGB collection (page {usage_page:#x}) on {vid:#06x}")
