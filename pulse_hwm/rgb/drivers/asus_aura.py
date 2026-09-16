"""ASUS Aura driver — probe-first stub for the AuraServiceLib COM surface.

Owner has no ASUS hardware, so this phase ships the DETECTION + the frozen
driver slot only; `probe` finds the Aura SDK service/DLL that Armoury Crate
installs and reports an install hint. `open()` raises a dedicated
NotHaloError to make the NOT-IMPLEMENTED case loud instead of silent — a
probe-only driver must never pretend to control devices.

Writing the real COM surface (AuraServiceLib.AuraSDKConfig) is a later
phase, gated on hardware availability.
"""

from __future__ import annotations

from pathlib import Path

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver

DLL_NAME = "AURA_SDK.dll"  # the classic surface; COM lib arrives later
_DLL_HINTS = (
    Path(r"C:\Program Files (x86)\ASUS\Armoury Crate"),
    Path(r"C:\Program Files\ASUS\Armoury Crate"),
    Path(r"C:\Windows\System32\AURA_SDK_Cpp"),
)


class AuraNotImplementedError(RuntimeError):
    """Raised at open(): the probe found Armoury Crate but the COM surface
    is not written yet (no ASUS hardware to verify against)."""


def _find_dll() -> Path | None:
    for path in _DLL_HINTS:
        candidate = path / DLL_NAME
        if candidate.exists():
            return candidate
    return None


class ASUSAuraDriver(RgbDriver):
    driver_id = "asus_aura"
    name = "ASUS Aura"
    version = "1"
    requires_admin = False
    requires_app = "Armoury Crate + Aura SDK module"

    def __init__(self, dll_path=None):
        self._dll_path = dll_path
        self.last_error: str = ""

    # ── abstract contract methods: LOUD until ASUS hardware lands ───────
    def open(self) -> None:  # noqa: D102 — placeholder contract surface
        raise AuraNotImplementedError(
            "ASUS Aura COM surface not implemented yet (needs ASUS hardware)"
        )

    def devices(self):  # noqa: D102 — never enumerate without a surface
        raise AuraNotImplementedError("ASUS Aura devices() not implemented")

    def set_frame(self, device_id: str, colors) -> bool:
        raise AuraNotImplementedError("ASUS Aura set_frame() not implemented")

    def probe(self) -> ProbeResult:
        # injection point for tests; real lookup otherwise
        path = self._dll_path or _find_dll()
        if path is None or not Path(path).exists():
            return ProbeResult(
                False,
                f"{DLL_NAME} not found — install Armoury Crate with the Aura "
                "SDK module",
                needs_install="Armoury Crate",
            )
        # available=True would be a MISTAKE for a stub that cannot drive
        # LEDs: the registry would then select it as the automatic pickup
        # and fail at open(). Honest False + reason instead.
        return ProbeResult(False, "driver not active yet — needs ASUS hardware")
