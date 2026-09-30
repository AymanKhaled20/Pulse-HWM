"""Render engine — pure frame production, zero Qt.

Runs (via worker.py) as the tick body on the rgb-engine QThread. Given a
driver, an effect catalog, and per-device effect assignments, one tick()
renders a frame per enabled device and pushes it to the driver.

Mode resolution (off/effects/reactive/override) lives in manager.py (phase
5): the engine is deliberately mode-agnostic — it renders whatever
assignments it was given and nothing else. Keeping modes out of the engine
is why the whole layer stays unit-testable with a FakeDriver and no threads.

Brightness: applied here by scaling colors (drivers without a brightness
API just receive dimmer frames); drivers WITH a native brightness API still
work — the scaling is simply redundant-but-harmless for them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pulse_hwm.rgb.drivers.base import RgbDriver
from pulse_hwm.rgb.effects.base import Effect, EffectContext
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.model import RgbDevice


@dataclass
class DeviceAssignment:
    """One device's effect assignment (validated, engine-ready)."""

    effect_id: str
    params: dict[str, float | int | str] = field(default_factory=dict)
    enabled: bool = True


class RgbEngine:
    """Frame producer. Single-threaded by design: every call must come from
    the worker's own thread (QTimer on the rgb-engine QThread)."""

    def __init__(self, catalog: EffectCatalog | None = None) -> None:
        """Start with no driver and no devices; attach_driver() wires one up."""
        self.catalog = catalog or EffectCatalog()
        self._driver: RgbDriver | None = None
        self._devices: dict[str, RgbDevice] = {}
        self._assignments: dict[str, DeviceAssignment] = {}
        self.brightness: int = 100
        self._sensors: dict = {}
        self._started: float | None = None
        self._last_tick_monotonic: float | None = None
        # diagnostics surfaced through the worker's signals (no Qt here)
        self.last_error: str = ""
        # what the RGB tab's live preview shows: the exact (brightness-
        # scaled) frame last handed to the driver, and the current problem
        # per device ("" / missing = fine). A device that starts working
        # again drops out of device_errors on its next good frame.
        self.last_frames: dict[str, list] = {}
        self.device_errors: dict[str, str] = {}

    # ── wiring ──────────────────────────────────────────────────────────
    def attach_driver(self, driver: RgbDriver) -> list[RgbDevice]:
        """Switch drivers: close the old, open + enumerate the new.
        Raises propagate — the worker catches and reports via signal."""
        self._close_driver()
        driver.open()
        self._driver = driver
        self._devices = {d.device_id: d for d in driver.devices()}
        self._started = time.monotonic()
        self._last_tick_monotonic = None
        return list(self._devices.values())

    def detach_driver(self) -> None:
        self._close_driver()

    def _close_driver(self) -> None:
        """Release the driver and forget everything tied to its devices."""
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:
                pass  # best-effort release, mirrors driver.close() contract
        self._driver = None
        self._devices = {}
        self.last_frames = {}
        self.device_errors = {}

    @property
    def attached(self) -> bool:
        return self._driver is not None

    @property
    def driver(self) -> RgbDriver | None:
        return self._driver

    def poll_driver(self) -> list[RgbDevice] | None:
        """Ask the driver whether its device list changed (unplug/replug).
        Returns the NEW device list when it did, None otherwise."""
        if self._driver is None or not self._driver.poll_changes():
            return None
        self._devices = {d.device_id: d for d in self._driver.devices()}
        # forget preview/error state of devices that went away
        self.last_frames = {
            k: v for k, v in self.last_frames.items() if k in self._devices
        }
        self.device_errors = {
            k: v for k, v in self.device_errors.items() if k in self._devices
        }
        return list(self._devices.values())

    def device_ids(self) -> list[str]:
        return list(self._devices)

    def devices(self) -> list[RgbDevice]:
        return list(self._devices.values())

    # ── assignment / settings ───────────────────────────────────────────
    def set_assignment(
        self, device_id: str, assignment: DeviceAssignment | None
    ) -> None:
        """Set (or with None, remove) the effect one device should render."""
        if assignment is None:
            self._assignments.pop(device_id, None)
            # not driven any more: nothing to preview, nothing failing
            self.last_frames.pop(device_id, None)
            self.device_errors.pop(device_id, None)
        else:
            self._assignments[device_id] = assignment

    def assignment(self, device_id: str) -> DeviceAssignment | None:
        return self._assignments.get(device_id)

    def set_brightness(self, pct: int) -> None:
        self.brightness = max(0, min(100, int(pct)))

    def set_sensors(self, sensors: dict) -> None:
        """Latest hardware snapshot (UI thread reads collector.updated,
        pushes here via the worker). Keys mirror the snapshot: cpu_temp,
        gpu_temp, mem_pct, max_temp."""
        self._sensors = dict(sensors or {})

    def _factor(self) -> float:
        return self.brightness / 100.0

    # ── the render tick ────────────────────────────────────────────────
    def tick(self) -> int:
        """Render + push one frame per enabled, assigned device. Returns the
        number of frames actually accepted by the driver."""
        self.last_error = ""
        if self._driver is None or self._started is None:
            return 0

        now_mono = time.monotonic()
        dt = 0.0
        if self._last_tick_monotonic is not None:
            dt = max(0.0, now_mono - self._last_tick_monotonic)
        self._last_tick_monotonic = now_mono
        now = now_mono - self._started

        applied = 0
        for device_id, device in self._devices.items():
            assignment = self._assignments.get(device_id)
            if not (assignment and assignment.enabled):
                # not driven any more → forget its preview AND any old error,
                # or the tab keeps showing PROBLEM for a device we don't touch
                self.last_frames.pop(device_id, None)
                self.device_errors.pop(device_id, None)
                continue
            effect = self.catalog.get(assignment.effect_id)
            if effect is None:
                continue  # unknown effect id (post-downgrade) → skip device
            try:
                frame = self._render_frame(effect, device, assignment, now, dt)
                self.last_frames[device_id] = frame
                accepted = self._driver.set_frame(device_id, frame)
                if accepted:
                    applied += 1
                    self.device_errors.pop(device_id, None)
                else:
                    detail = str(
                        getattr(self._driver, "last_error", "") or "frame rejected"
                    )
                    self.last_error = f"{device_id}: {detail}"
                    self.device_errors[device_id] = detail
            except Exception as exc:
                # a misbehaving device/effect must never stop the whole tick
                self.last_error = f"{device_id}: {exc}"
                self.device_errors[device_id] = str(exc)
        return applied

    def _render_frame(
        self,
        effect: Effect,
        device: RgbDevice,
        assignment: DeviceAssignment,
        now: float,
        dt: float,
    ) -> list:
        # params re-validated against spec at render time via ctx.param()
        ctx = EffectContext(
            device=device,
            params=dict(assignment.params),
            now=now,
            dt=dt,
            sensors=dict(self._sensors),
        )
        frame = effect.render(ctx)
        if self.brightness >= 100:
            return frame
        factor = self._factor()
        return [color.scaled(factor) for color in frame]
