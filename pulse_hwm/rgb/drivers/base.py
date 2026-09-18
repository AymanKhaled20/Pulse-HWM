"""Driver contract — frozen like the effect contract.

Key rules (from the phase plan):
  * probe() NEVER raises and has no side effects: it answers "can this
    driver plausibly work on this machine right now?" (DLL present, vendor
    app running, dongle visible). The UI shows reason/needs_install when
    unavailable.
  * set_frame() is the one universal call: whole-device drivers just fill
    from a representative color; per-key drivers write every LED.
  * set_brightness() is optional — drivers without a brightness API return
    False and the engine derives brightness by scaling colors instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pulse_hwm.rgb.model import RgbColor, RgbDevice


@dataclass(frozen=True)
class ProbeResult:
    """Availability answer. available=False is a normal, expected state
    (missing vendor app), never an error."""

    available: bool
    reason: str = ""  # human-readable why-not ("" when available)
    needs_install: str = ""  # app name to install, "" when available

    def unavailable(self, reason: str, needs_install: str = "") -> "ProbeResult":
        # convenience for drivers — kept here so reason strings stay structured
        return ProbeResult(False, reason, needs_install)


class RgbDriver(ABC):
    """One RGB ecosystem or device family (Aula F75, Razer Chroma, …)."""

    driver_id: str = ""
    name: str = ""
    version: str = "0"
    requires_admin: bool = False  # UI hint only
    requires_app: str = ""  # e.g. "Razer Synapse" — shown when probe fails

    @abstractmethod
    def probe(self) -> ProbeResult:
        """Availability check. Must not raise, must not alter device state."""

    @abstractmethod
    def open(self) -> None:
        """Connect / initialize the session. May raise; caller degrades."""

    def close(self) -> None:
        """Release the session. Best-effort: should not raise."""

    @abstractmethod
    def devices(self) -> list[RgbDevice]:
        """Enumerate controllable devices. Only valid after open()."""

    @abstractmethod
    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        """Push one frame. Returns True when the device accepted it."""

    def set_brightness(self, device_id: str, pct: int) -> bool:
        """Native brightness control when the SDK offers one."""
        return False
