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


BUILTIN_EFFECTS: tuple[Effect, ...] = (StaticEffect(), BreatheEffect())
