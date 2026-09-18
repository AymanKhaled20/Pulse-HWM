"""Effect contract + built-in STATIC/BREATHE render tests — deterministic,
no Qt, no I/O. Time is stepped via EffectContext.now."""

from __future__ import annotations

from pulse_hwm.rgb.effects.base import EffectContext, ParamSpec
from pulse_hwm.rgb.effects.builtin import BreatheEffect, StaticEffect
from pulse_hwm.rgb.model import RgbColor, RgbDevice


def make_device(leds: int = 8) -> RgbDevice:
    return RgbDevice(device_id="d1", name="Test", driver_id="fake", leds=leds)


def make_ctx(
    device: RgbDevice, now: float = 0.0, params: dict | None = None
) -> EffectContext:
    return EffectContext(device=device, params=params or {}, now=now)


class TestParamSpec:
    def test_float_clamped(self):
        spec = ParamSpec("speed", "SPEED", "float", 0.5, 0.0, 1.0)
        assert spec.validate(2.5) == 1.0
        assert spec.validate(-1) == 0.0
        assert spec.validate("0.75") == 0.75
        assert spec.validate("junk") == 0.5  # degrades to default

    def test_int_clamped(self):
        spec = ParamSpec("count", "COUNT", "int", 4, 1, 10)
        assert spec.validate(99) == 10
        assert spec.validate(0) == 1
        assert spec.validate("7") == 7

    def test_choice(self):
        spec = ParamSpec("dir", "DIR", "choice", "up", choices=("up", "down"))
        assert spec.validate("down") == "down"
        assert spec.validate("sideways") == "up"

    def test_color(self):
        spec = ParamSpec("color", "COLOR", "color", "#FFD400")
        assert spec.validate("#00FF41") == "#00FF41"
        # garbage hex → RgbColor black fallback (never raises)
        assert spec.validate("garbage") == "#000000"


class TestStaticEffect:
    def test_uniform_frame(self):
        effect = StaticEffect()
        frame = effect.render(make_ctx(make_device(6), params={"color": "#00FF41"}))
        assert len(frame) == 6
        assert all(c == RgbColor(0, 255, 65) for c in frame)

    def test_defaults_when_no_params(self):
        effect = StaticEffect()
        frame = effect.render(make_ctx(make_device(3)))
        assert all(c == RgbColor(255, 212, 0) for c in frame)

    def test_min_one_led(self):
        # leds == 0 (unknown count) still yields a renderable frame
        effect = StaticEffect()
        assert len(effect.render(make_ctx(make_device(0)))) == 1


class TestBreatheEffect:
    def test_brightness_varies_over_time(self):
        effect = BreatheEffect()
        device = make_device(4)
        params = {"speed": 0.5}
        dark = effect.render(make_ctx(device, now=0.0, params=params))
        bright = effect.render(make_ctx(device, now=1.5, params=params))
        assert len(dark) == 4
        # a sine breathe must actually change brightness across a period
        assert dark[0] != bright[0]

    def test_never_fully_black_or_full(self):
        # scaled floor 0.15 keeps the device visible at the sine trough
        effect = BreatheEffect()
        frame = effect.render(make_ctx(make_device(2), now=0.75, params={"speed": 0.5}))
        assert all(c.r + c.g + c.b > 0 for c in frame)

    def test_brighter_faster(self):
        # higher speed → shorter period → more cycles in the same window
        effect = BreatheEffect()
        device = make_device(1)
        slow = effect.render(make_ctx(device, now=1.0, params={"speed": 0.1}))
        fast = effect.render(make_ctx(device, now=1.0, params={"speed": 1.0}))
        assert slow[0] != fast[0]
