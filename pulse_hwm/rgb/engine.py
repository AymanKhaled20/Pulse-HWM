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
        self.catalog = catalog or EffectCatalog()
        self._driver: RgbDriver | None = None
        self._devices: dict[str, RgbDevice] = {}
        self._assignments: dict[str, DeviceAssignment] = {}
        self.brightness: int = 100
        self._started: float | None = None
        self._last_tick_monotonic: float | None = None
        # diagnostics surfaced through the worker's signals (no Qt here)
        self.last_error: str = ""

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
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:
                pass  # best-effort release, mirrors driver.close() contract
        self._driver = None
        self._devices = {}

    @property
    def attached(self) -> bool:
        return self._driver is not None

    def device_ids(self) -> list[str]:
        return list(self._devices)

    def devices(self) -> list[RgbDevice]:
        return list(self._devices.values())

    # ── assignment / settings ───────────────────────────────────────────
    def set_assignment(
        self, device_id: str, assignment: DeviceAssignment | None
    ) -> None:
        if assignment is None:
            self._assignments.pop(device_id, None)
        else:
            self._assignments[device_id] = assignment

    def assignment(self, device_id: str) -> DeviceAssignment | None:
        return self._assignments.get(device_id)

    def set_brightness(self, pct: int) -> None:
        self.brightness = max(0, min(100, int(pct)))

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
                continue
            effect = self.catalog.get(assignment.effect_id)
            if effect is None:
                continue  # unknown effect id (post-downgrade) → skip device
            try:
                frame = self._render_frame(effect, device, assignment, now, dt)
                if self._driver.set_frame(device_id, frame):
                    applied += 1
            except Exception as exc:
                # a misbehaving device/effect must never stop the whole tick
                self.last_error = f"{device_id}: {exc}"
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
            device=device, params=dict(assignment.params), now=now, dt=dt
        )
        frame = effect.render(ctx)
        if self.brightness >= 100:
            return frame
        factor = self._factor()
        return [color.scaled(factor) for color in frame]
