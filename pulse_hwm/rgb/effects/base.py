"""Effect contract — pure rendering, no Qt, no I/O.

An Effect turns (time, device geometry, params, sensor context) into a frame:
one RgbColor per LED. Effects never talk to drivers or hardware; the engine
feeds rendered frames to drivers. This separation is what keeps every effect
unit-testable and lets the same effect drive a 5-LED strip or a per-key
keyboard.

EffectContext carries sensor/alert state the engine collected elsewhere, so
reactive effects (phases 15-16) read values injected here — effects never
poll psutil themselves (no I/O in render loops, ever).

ParamSpec describes one effect parameter for UI generation and validation:
declarative JSON imports (phase 12) are checked against the same spec, so
imported effects can never pass out-of-range values into render code.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pulse_hwm.rgb.model import RgbColor, RgbDevice


@dataclass(frozen=True)
class ParamSpec:
    """One effect parameter: type, range, default — the validation AND the
    future auto-generated UI control in one place."""

    key: str
    label: str
    kind: str  # "float" | "int" | "color" | "choice"
    default: float | int | str
    minimum: float = 0.0
    maximum: float = 1.0
    choices: tuple[str, ...] = ()

    def validate(self, value: float | int | str) -> float | int | str:
        """Return a safe value, or the default on any violation. Never
        raises — imported/legacy params must degrade, not crash the engine."""
        try:
            if self.kind == "float":
                number = float(value)
                return max(self.minimum, min(self.maximum, number))
            if self.kind == "int":
                number = int(float(value))
                return int(max(self.minimum, min(self.maximum, number)))
            if self.kind == "choice":
                text = str(value)
                return text if text in self.choices else str(self.default)
            if self.kind == "color":
                return RgbColor.from_hex(str(value)).to_hex()
        except (TypeError, ValueError):
            pass
        return self.default


@dataclass
class EffectContext:
    """Per-render inputs handed to every effect by the engine.

    now/dt are engine-clock driven (not wall-clock) so tests can step time
    deterministically. sensors uses "safe" keys (cpu_temp, gpu_temp, mem_pct,
    max_temp) that are always present, defaulting to None when unknown.
    last_alert is (level, seconds_remaining) or None — reactive effects
    decide how to flash; the manager owns the countdown.
    """

    device: RgbDevice
    params: dict[str, float | int | str] = field(default_factory=dict)
    now: float = 0.0  # seconds since engine start
    dt: float = 0.016  # seconds since previous frame
    sensors: dict[str, float | None] = field(default_factory=dict)
    last_alert: tuple[str, float] | None = None

    def sensor(self, name: str) -> float | None:
        value = self.sensors.get(name)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def param(self, spec: ParamSpec) -> float | int | str:
        """Validated parameter for this render (spec.default when absent)."""
        return spec.validate(self.params.get(spec.key, spec.default))


class Effect(ABC):
    """One effect implementation. Subclasses are singletons registered in
    the catalog; render() must be pure (no I/O, no global state mutation)
    because the engine may call it at 60 FPS across many devices."""

    effect_id: str = ""
    name: str = ""
    description: str = ""

    params: tuple[ParamSpec, ...] = ()

    def __init__(self) -> None:
        self._start = time.monotonic()

    @abstractmethod
    def render(self, ctx: EffectContext) -> list[RgbColor]:
        """One frame: len(output) == ctx.device.leds (min 1)."""

    def frame_size(self, ctx: EffectContext) -> int:
        return max(1, ctx.device.leds)
