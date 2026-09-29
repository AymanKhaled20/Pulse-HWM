"""Driver registry.

Each driver family lands with exactly one registry entry (built in
controllers/rgb_controller.py), so no driver touches another's code. The
vendor-SDK, raw-HID and native AULA drivers were all removed in favor of the
OpenRGB backend, which today is the only real entry.

User drop-ins (%LOCALAPPDATA%/PulseHWM/plugins/rgb/*.py) are loaded in a
later phase behind the rgb_allow_external_plugins toggle; the discovery
shape is already prepared here so that phase is registry-only work.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.model import RgbColor, RgbDevice

__all__ = ["ProbeResult", "RgbDriver", "DriverRegistry", "FakeDriver"]

# Real drivers append here in their own phases ((driver_class, ...) order
# defines UI listing order).
DRIVER_CLASSES: tuple[type[RgbDriver], ...] = ()


class FakeDriver(RgbDriver):
    """In-memory driver — always available, records every frame. Used by
    engine/manager tests and as a UI smoke target; never shipped as a
    user-visible choice (filtered by driver_id, not by special-casing)."""

    driver_id = "fake"
    name = "Fake (test)"
    version = "1"

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.frames: dict[str, list[list[RgbColor]]] = {}
        self.fail_device: str | None = None  # when set, set_frame returns False

    def probe(self) -> ProbeResult:
        return ProbeResult(True)

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(
                device_id="fake:0", name="FAKE STRIP", driver_id=self.driver_id, leds=8
            ),
            RgbDevice(
                device_id="fake:1", name="FAKE PAD", driver_id=self.driver_id, leds=4
            ),
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        if device_id == self.fail_device:
            return False
        self.frames.setdefault(device_id, []).append(list(colors))
        return True


class DriverRegistry:
    """Instantiates and holds drivers. Broken/out-of-tree driver classes are
    isolated: one raising constructor or probe() must not kill discovery."""

    def __init__(
        self, driver_classes: tuple[type[RgbDriver], ...] = DRIVER_CLASSES
    ) -> None:
        self._classes = driver_classes
        self._drivers: dict[str, RgbDriver] = {}
        # last load() problems, for the UI status strip ([] = none)
        self.last_errors: list[str] = []

    def load(self) -> list[RgbDriver]:
        """Instantiate all entries; skip + record failures. Idempotent.

        Entries may be driver CLASSES (the registry constructs them) or
        pre-built INSTANCES — the RGB controller hands in OpenRgbDriver as
        an instance because it needs a constructor argument (the port).
        """
        self._drivers.clear()
        errors: list[str] = []
        for entry in self._classes:
            is_class = isinstance(entry, type)
            label = entry.__name__ if is_class else type(entry).__name__
            try:
                driver = entry() if is_class else entry
                if not driver.driver_id:
                    errors.append(f"{label}: empty driver_id")
                    continue
                if driver.driver_id in self._drivers:
                    # first registration wins: a later entry shadowing an
                    # already-built instance would silently swap transports
                    errors.append(f"{label}: duplicate driver_id")
                    continue
                self._drivers[driver.driver_id] = driver
            except Exception as exc:  # one bad import/ctor must not stop all
                errors.append(f"{label}: {exc}")
        self.last_errors = errors
        return self.available()

    def register(self, driver: RgbDriver) -> None:
        self._drivers[driver.driver_id] = driver

    def get(self, driver_id: str) -> RgbDriver | None:
        return self._drivers.get(driver_id)

    def available(self) -> list[RgbDriver]:
        """Drivers whose probe() succeeds; probe raising is isolated."""
        result = []
        for driver in list(self._drivers.values()):
            try:
                if driver.probe().available:
                    result.append(driver)
            except Exception:
                continue
        return result
