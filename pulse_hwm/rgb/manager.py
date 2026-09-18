"""Mode layer — decides WHAT renders, never renders it.

RgbEngine (phase 4) is mode-agnostic: it renders assignments and pushes
frames. This module owns the single source of truth for who drives the
hardware:

    off       — plan clears every device (engine idles; vendor software wins)
    effects   — per-device assignments from the rgb_device_assignment blob
    reactive  — every device follows hardware state (temp/alert)
    override  — every device gets the forced color/effect; beats everything

Design rules honored here:
  * ModePlanner is PURE (no Qt, no I/O, no clocks inside) — all decisions
    are unit-testable.
  * Alerts only matter in reactive mode — override is manual and wins,
    effects mode ignores hardware by definition.
  * The reactive effect ids are constants the reactive effect phase (15/16)
    must register under the EXACT same ids; until then the engine skips
    them as unknown, which is the safe degrade.
  * RgbManager is Qt-free too (hooks instead of signals). Phase 8 wires
    worker signals to the hooks in app.py — manager stays testable without
    a QApplication, matching the repo's "pure logic, no Qt" convention.
"""

from __future__ import annotations

import json
import threading
import time

from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.engine import DeviceAssignment

# wired by phase 15/16; the planner references ids only
REACTIVE_TEMP_EFFECT_ID = "reactive_temp"
REACTIVE_ALERT_EFFECT_ID = "reactive_alert"

ALERT_PARAM_COLOR = "color"


class ModePlan:
    """Outcome of one planner pass: assignments keyed by device_id.
    None value = engine must clear the device (stop driving it)."""

    __slots__ = ("assignments",)

    def __init__(self, assignments: dict[str, DeviceAssignment | None]) -> None:
        self.assignments = assignments

    def is_noop(self, other: "ModePlan | None") -> bool:
        """True when pushing `self` would change nothing (used to avoid
        spamming recompute)."""
        if other is None:
            return False
        return self.assignments == other.assignments


def parse_assignment_blob(blob: str, catalog, driver_id: str, device_id: str):
    """rgb_device_assignment JSON → DeviceAssignment for one device, or
    None. Blob layout: {f"{driver_id}/{device_id}": {"effect": id, "params":
    {}, "enabled": true}}. Corrupt values degrade to None instead of
    crashing — the blob is user-editable storage."""
    try:
        data = json.loads(blob or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(f"{driver_id}/{device_id}")
    if not isinstance(entry, dict):
        return None
    effect_id = str(entry.get("effect") or "")
    if catalog.get(effect_id) is None:
        return None  # unknown/downgraded effect id → skip this device
    raw_params = entry.get("params") or {}
    if not isinstance(raw_params, dict):
        raw_params = {}
    params = catalog.validate_params(effect_id, raw_params)
    enabled = bool(entry.get("enabled", True))
    return DeviceAssignment(effect_id, params=params, enabled=enabled)


class ModePlanner:
    """One pure decision pass. Constructed per plan; holds no state."""

    def __init__(
        self,
        mode: str,
        driver_id: str,
        device_ids: list[str],
        catalog,
        override_effect_id: str,
        override_color: str,
        override_speed: int = 50,
        reactive_effect_id: str = REACTIVE_TEMP_EFFECT_ID,
        alert_active: bool = False,
        alert_color: str = "#FF3B30",
        reactive_sensor: str = "cpu",
        reactive_low_c: float = 40.0,
        reactive_high_c: float = 85.0,
        reactive_low_color: str = "#00FF41",
        reactive_high_color: str = "#FF3B30",
    ) -> None:
        self.mode = mode
        self.driver_id = driver_id
        self.device_ids = list(device_ids)
        self.catalog = catalog
        self.override_effect_id = override_effect_id
        self.override_color = override_color
        # 0-100 UI speed → 0-1 effect-relative (specs use 0..1 floats; the
        # per-effect validation drops the key for speed-less effects)
        self.override_speed = max(0.0, min(1.0, int(override_speed) / 100.0))
        self.reactive_effect_id = reactive_effect_id
        self.alert_active = alert_active
        self.alert_color = alert_color
        # the reactive contract: effect sensor key derives FROM setting
        # cpu|gpu|mem|max_temp → EffectContext keys (signals the sensor the
        # reactive_temp effect should read)
        self._sensor_key_map = {
            "cpu": "cpu_temp",
            "gpu": "gpu_temp",
            "mem": "mem_pct",
            "max_temp": "max_temp",
        }
        self.reactive_sensor_key = self._sensor_key_map.get(
            str(reactive_sensor), "cpu_temp"
        )
        self.reactive_low_c = float(reactive_low_c)
        self.reactive_high_c = float(reactive_high_c)
        self.reactive_low_color = reactive_low_color
        self.reactive_high_color = reactive_high_color

    def reactive_params(self) -> dict:
        """reactive_temp spec keys (low_c/high_c/low_color/high_color/
        sensor) validated per-effect at build time."""
        return {
            "sensor": self.reactive_sensor_key,
            "low_c": self.reactive_low_c,
            "high_c": self.reactive_high_c,
            "low_color": self.reactive_low_color,
            "high_color": self.reactive_high_color,
        }

    def plan(self, assignment_loader=lambda device_id: None) -> ModePlan:
        """assignment_loader(device_id) → DeviceAssignment for effects
        mode; injectable so the blob parsing stays out of tests."""
        if self.mode == "off":
            return ModePlan({d: None for d in self.device_ids})
        if self.mode == "override":
            if self.catalog.get(self.override_effect_id) is None:
                # unknown override effect (post-downgrade): refuse to take
                # control rather than guessing
                return ModePlan({d: None for d in self.device_ids})
            params = self.catalog.validate_params(
                self.override_effect_id,
                {"color": self.override_color, "speed": self.override_speed},
            )
            assignment = DeviceAssignment(self.override_effect_id, params=params)
            return ModePlan({d: assignment for d in self.device_ids})
        if self.mode == "reactive":
            # alert flash (when an alert is recent) wins over the temp map;
            # ALERT param color keys the flash effect (phase 16)
            effect_id = (
                REACTIVE_ALERT_EFFECT_ID
                if self.alert_active
                else self.reactive_effect_id
            )
            if self.catalog.get(effect_id) is None:
                # reactive effect not implemented yet → nothing to render;
                # NOT a control takeover, hardware keeps its current state
                return ModePlan({})
            params = self.catalog.validate_params(
                effect_id, {"color": self.alert_color, **self.reactive_params()}
            )
            return ModePlan(
                {d: DeviceAssignment(effect_id, params=params) for d in self.device_ids}
            )
        if self.mode == "effects":
            planned: dict[str, DeviceAssignment | None] = {}
            for device_id in self.device_ids:
                planned[device_id] = assignment_loader(device_id)
            return ModePlan(planned)
        # unknown mode string → treat as off (same degradation as settings)
        return ModePlan({d: None for d in self.device_ids})


class AlertClock:
    """Alert recency tracker (monotonic, injectable clock). The manager
    feeds handle_alert(level) here; planner reads active()."""

    def __init__(self, hold_ms: int, clock=time.monotonic) -> None:
        self.hold_ms = max(0, int(hold_ms))
        self._clock = clock
        self._deadline: float | None = None

    def trigger(self) -> None:
        self._deadline = self._clock() + self.hold_ms / 1000.0

    def set_hold_ms(self, hold_ms: int) -> None:
        self.hold_ms = max(0, int(hold_ms))

    def active(self) -> bool:
        return self._deadline is not None and self._clock() < self._deadline


class RgbManager:
    """Qt-free controller glue: reads app settings, runs the planner, and
    pushes the resulting assignments through a single hook.

    app.py sets `apply_assignments` to a function that forwards the dict
    into the worker's thread via a Signal→slot connection (that connection
    is Qt's job, not this module's — one more reason manager.py stays pure).
    reconsider() is cheap enough to run on every relevant UI event without
    timers; plan no-op detection prevents redundant cross-thread pushes.
    """

    def __init__(self, db, catalog=None, alert_hold_ms: int = 4000) -> None:
        self._db = db
        self._catalog = catalog or EffectCatalog()
        self._driver_id = ""
        self._device_ids: list[str] = []
        self._last_plan: ModePlan | None = None
        self._alert_clock = AlertClock(alert_hold_ms)
        self._was_alert_active = False
        self._lock = threading.Lock()  # reconsider() runs on UI AND worker
        # hook injected by app.py: fn({device_id: DeviceAssignment | None})
        self.apply_assignments = None

    def set_driver(self, driver_id: str, device_ids: list[str]) -> None:
        self._driver_id = driver_id
        self._device_ids = list(device_ids)
        self._last_plan = None  # driver switch always means a fresh plan

    def clear_driver(self) -> None:
        self.set_driver("", [])

    def reconsider(self) -> bool:
        """One planner pass against the CURRENT stored settings. Returns
        True when a new plan was pushed to the hook. Locked: reachable from
        both the UI thread (mode changed) and the worker thread (devices
        changed), and both read/compare/push `self._last_plan`."""
        with self._lock:
            return self._reconsider_locked()

    def force_reconsider(self) -> bool:
        """APPLY NOW path: re-push the plan even when it matches the last
        one. The engine re-renders from the new assignments, which is what
        the RGB tab's explicit apply button promises the user."""
        with self._lock:
            self._last_plan = None
            return self._reconsider_locked()

    def _reconsider_locked(self) -> bool:
        from pulse_hwm import app_settings

        settings = app_settings.load(self._db)
        if not self._device_ids:
            return False
        planner = ModePlanner(
            mode=settings.rgb_mode,
            driver_id=self._driver_id,
            device_ids=self._device_ids,
            catalog=self._catalog,
            override_effect_id=settings.rgb_override_effect,
            override_color=settings.rgb_override_color,
            override_speed=settings.rgb_override_speed,
            alert_active=self._alert_clock.active(),
            alert_color=settings.rgb_alert_color,
            reactive_sensor=settings.rgb_reactive_source,
            reactive_low_c=settings.rgb_temp_low_c,
            reactive_high_c=settings.rgb_temp_high_c,
            reactive_low_color=settings.rgb_temp_low_color,
            reactive_high_color=settings.rgb_temp_high_color,
        )
        loader = self._assignment_loader(settings.rgb_device_assignment)
        plan = planner.plan(assignment_loader=loader)
        if plan.is_noop(self._last_plan):
            return False
        self._last_plan = plan
        if self.apply_assignments is not None:
            self.apply_assignments(dict(plan.assignments))
        return True

    def handle_alert(self) -> None:
        """Alert flash entry point (wired to alerts in phase 16)."""
        self._alert_clock.trigger()
        self.reconsider()

    def maintain(self) -> None:
        """Alert expiry sweep: when a held alert expires, replan once so
        reactive devices go back to the temperature map. Cheap enough to
        run on a 250 ms scheduler tick."""
        was_active = self._was_alert_active
        now_active = self._alert_clock.active()
        if was_active and not now_active:
            self.reconsider()
        self._was_alert_active = now_active

    def _assignment_loader(self, blob: str):
        from functools import partial

        from pulse_hwm.rgb.manager import parse_assignment_blob

        return partial(parse_assignment_blob, blob, self._catalog, self._driver_id)
