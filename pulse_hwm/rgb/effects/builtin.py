"""Built-in effects. Phase 2 ships STATIC + BREATHE; later phases add
RAINBOW/WAVE/SPECTRUM/STROBE and the reactive pair — each addition is pure
code here, no contract changes.

Rendering notes:
  * STATIC fills the frame — whole-device drivers get one color, per-key
    devices get a uniform map (visually identical, contract unchanged).
  * BREATHE is a sinusoidal brightness pulse; the sine is evaluated from
    ctx.now so rendering is deterministic and test-steppable.
"""

from __future__ import annotations

import math

from pulse_hwm.rgb.effects.base import Effect, EffectContext, ParamSpec
from pulse_hwm.rgb.model import RgbColor

_SPEED_SPEC = ParamSpec(
    key="speed", label="SPEED", kind="float", default=0.5, minimum=0.05, maximum=1.0
)


class StaticEffect(Effect):
    effect_id = "static"
    name = "STATIC"
    description = "One solid color across the whole device."

    params = (ParamSpec(key="color", label="COLOR", kind="color", default="#FFD400"),)

    def render(self, ctx: EffectContext) -> list[RgbColor]:
        color = RgbColor.from_hex(str(ctx.param(self.params[0])))
        return [color] * self.frame_size(ctx)


class BreatheEffect(Effect):
    effect_id = "breathe"
    name = "BREATHE"
    description = "Slow brightness pulse in one color."

    params = (
        ParamSpec(key="color", label="COLOR", kind="color", default="#FFD400"),
        _SPEED_SPEC,
    )

    def render(self, ctx: EffectContext) -> list[RgbColor]:
        color = RgbColor.from_hex(str(ctx.param(self.params[0])))
        speed = float(ctx.param(self.params[1]))
        # 0.05..1.0 → period 6s..0.4s; sine phase from engine clock
        period = 6.0 - 5.6 * (speed - 0.05) / 0.95
        wave = 0.5 + 0.5 * math.sin(2.0 * math.pi * (ctx.now / period))
        return [color.scaled(0.15 + 0.85 * wave)] * self.frame_size(ctx)


class ReactiveTempEffect(Effect):
    """CPU/GPU temp → color gradient. Sensors come from EffectContext (the
    engine's snapshots — never polled here). Missing sensor = transparent
    black frame is BAD at the reactive layer (dark keyboard mid-tick); the
    manager keeps the previous assignment instead, so a missing sensor
    effectively freezes the last color. Rendering: solid color across the
    frame; temperature drives WHICH color."""

    effect_id = "reactive_temp"
    name = "REACTIVE TEMP"
    description = "Hardware temperature mapped to a color gradient."

    params = (
        ParamSpec("low_c", "TEMP LOW", "float", 40.0, 0.0, 100.0),
        ParamSpec("high_c", "TEMP HIGH", "float", 85.0, 0.0, 150.0),
        ParamSpec("low_color", "COOL COLOR", "color", "#00FF41"),
        ParamSpec("high_color", "HOT COLOR", "color", "#FF3B30"),
        ParamSpec(
            "sensor",
            "SENSOR",
            "choice",
            "cpu_temp",
            choices=("cpu_temp", "gpu_temp", "mem_pct", "max_temp"),
        ),
    )

    def render(self, ctx: EffectContext) -> list[RgbColor]:
        spec_by_key = {spec.key: spec for spec in self.params}
        low = float(ctx.param(spec_by_key["low_c"]))
        high = float(ctx.param(spec_by_key["high_c"]))
        sensor = str(ctx.param(spec_by_key["sensor"]))
        value = ctx.sensor(sensor)
        if value is None:
            return [SensorMissingColor()] * self.frame_size(ctx)
        position = max(0.0, min(1.0, (value - low) / max(1e-9, high - low)))
        low_color = RgbColor.from_hex(str(ctx.param(spec_by_key["low_color"])))
        high_color = RgbColor.from_hex(str(ctx.param(spec_by_key["high_color"])))
        color = _lerp_color(low_color, high_color, position)
        return [color] * self.frame_size(ctx)


def SensorMissingColor() -> RgbColor:
    # dim gray-yellow: visibly 'no data' but not dead-black
    return RgbColor(40, 40, 12)


def _lerp_color(low: RgbColor, high: RgbColor, t: float) -> RgbColor:
    t = max(0.0, min(1.0, t))
    return RgbColor(
        round(low.r + (high.r - low.r) * t),
        round(low.g + (high.g - low.g) * t),
        round(low.b + (high.b - low.b) * t),
    )


class ReactiveAlertEffect(Effect):
    """Alert flash: full-brightness alert color while the AlertClock holds.
    The MANAGER owns the hold countdown (mode replans back to reactive_temp
    on expiry) — this effect only paints; no time-dependent logic here."""

    effect_id = "reactive_alert"
    name = "REACTIVE ALERT"
    description = "Alert-state flash; reverts when the hold expires."

    params = (ParamSpec(key="color", label="COLOR", kind="color", default="#FF3B30"),)

    def render(self, ctx: EffectContext) -> list[RgbColor]:
        color = RgbColor.from_hex(str(ctx.param(self.params[0])))
        # a sine-gated strobe at 4 Hz reads as ALERT; the hold countdown in
        # the manager decides when to stop rendering it at all
        wave = 0.5 + 0.5 * math.sin(2.0 * math.pi * (ctx.now * 2.5))
        return [color.scaled(0.5 + 0.5 * wave)] * self.frame_size(ctx)


BUILTIN_EFFECT_CLASSES: tuple[type[Effect], ...] = (
    StaticEffect,
    BreatheEffect,
    ReactiveTempEffect,
    ReactiveAlertEffect,
)
