"""Built-in effects: STATIC, BREATHE, RAINBOW, SPECTRUM, WAVE, plus the
reactive pair the mode planner drives. Each addition is pure code here, no
contract changes.

Rendering notes:
  * STATIC fills the frame — whole-device drivers get one color, per-key
    devices get a uniform map (visually identical, contract unchanged).
  * Every animated effect is evaluated from ctx.now (engine clock), so
    rendering is deterministic and test-steppable.
  * RAINBOW and WAVE move along the LED *index*. OpenRGB reports each
    device's LEDs zone by zone, so index order follows the physical strip /
    key rows closely enough and needs no layout geometry.
"""

from __future__ import annotations

import math

from pulse_hwm.rgb.color_math import hsv_to_rgb
from pulse_hwm.rgb.effects.base import Effect, EffectContext, ParamSpec
from pulse_hwm.rgb.model import MODE_RAINBOW, MODE_SPECTRUM, MODE_WAVE, RgbColor

_SPEED_SPEC = ParamSpec(
    key="speed", label="SPEED", kind="float", default=0.5, minimum=0.05, maximum=1.0
)
_COLOR_SPEC = ParamSpec(key="color", label="COLOR", kind="color", default="#FFD400")

# WAVE: the lit band covers this fraction of the strip, and LEDs outside it
# keep this much of the color so the device never looks switched off
_WAVE_BAND_WIDTH = 0.25
_WAVE_BASE_LEVEL = 0.08


def _cycles_per_second(speed: float) -> float:
    """SPEED 0.05..1.0 → loops per second (0.05 = one loop every 20 s,
    1.0 = one loop per second). One mapping for every moving effect so the
    same SPEED value feels the same across them."""
    return max(0.05, min(1.0, float(speed)))


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


class RainbowEffect(Effect):
    effect_id = MODE_RAINBOW
    name = "RAINBOW"
    description = "A full rainbow that scrolls along the LEDs."

    params = (_SPEED_SPEC,)

    def render(self, ctx: EffectContext) -> list[RgbColor]:
        """Spread the color wheel across the LEDs and rotate it over time."""
        count = self.frame_size(ctx)
        shift = ctx.now * _cycles_per_second(float(ctx.param(self.params[0])))
        # each LED sits at its own spot on the color wheel; adding `shift`
        # rotates the whole wheel, which reads as the rainbow moving
        return [
            hsv_to_rgb((index / count + shift) % 1.0, 1.0, 1.0)
            for index in range(count)
        ]


class SpectrumEffect(Effect):
    effect_id = MODE_SPECTRUM
    name = "SPECTRUM CYCLE"
    description = "The whole device fades through every color together."

    params = (_SPEED_SPEC,)

    def render(self, ctx: EffectContext) -> list[RgbColor]:
        """Paint every LED the same hue, stepping around the wheel over time."""
        hue = (ctx.now * _cycles_per_second(float(ctx.param(self.params[0])))) % 1.0
        return [hsv_to_rgb(hue, 1.0, 1.0)] * self.frame_size(ctx)


class WaveEffect(Effect):
    effect_id = MODE_WAVE
    name = "WAVE"
    description = "A band of your color sweeps across the LEDs."

    params = (_COLOR_SPEC, _SPEED_SPEC)

    def render(self, ctx: EffectContext) -> list[RgbColor]:
        """Draw a soft band of the chosen color that sweeps along the LEDs."""
        color = RgbColor.from_hex(str(ctx.param(self.params[0])))
        count = self.frame_size(ctx)
        # where the middle of the band is right now, 0..1 along the strip
        center = (ctx.now * _cycles_per_second(float(ctx.param(self.params[1])))) % 1.0
        frame: list[RgbColor] = []
        for index in range(count):
            position = (index + 0.5) / count
            # distance around a loop, so the band wraps from the last LED
            # back to the first instead of jumping
            distance = abs(position - center)
            distance = min(distance, 1.0 - distance)
            strength = max(0.0, 1.0 - distance / (_WAVE_BAND_WIDTH / 2.0))
            level = _WAVE_BASE_LEVEL + (1.0 - _WAVE_BASE_LEVEL) * strength
            frame.append(color.scaled(level))
        return frame


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
    user_selectable = False  # REACTIVE mode drives it with its own settings

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
    user_selectable = False  # only ever planned during an alert

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
    RainbowEffect,
    SpectrumEffect,
    WaveEffect,
    ReactiveTempEffect,
    ReactiveAlertEffect,
)
