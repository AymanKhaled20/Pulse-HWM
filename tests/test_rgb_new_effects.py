"""RAINBOW / SPECTRUM CYCLE / WAVE render tests, plus the catalog's
user-facing effect list. Deterministic: time is stepped via ctx.now."""

from __future__ import annotations

from pulse_hwm.rgb.effects.base import EffectContext
from pulse_hwm.rgb.effects.builtin import RainbowEffect, SpectrumEffect, WaveEffect
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.model import RgbColor, RgbDevice


def make_ctx(leds: int = 12, now: float = 0.0, params: dict | None = None):
    device = RgbDevice(device_id="d1", name="Test", driver_id="fake", leds=leds)
    return EffectContext(device=device, params=params or {}, now=now)


class TestRainbow:
    def test_one_color_per_led_and_all_different(self):
        frame = RainbowEffect().render(make_ctx(leds=12))
        assert len(frame) == 12
        assert len(set(frame)) == 12  # the whole wheel is spread out

    def test_moves_over_time(self):
        effect = RainbowEffect()
        first = effect.render(make_ctx(now=0.0, params={"speed": 0.5}))
        later = effect.render(make_ctx(now=0.4, params={"speed": 0.5}))
        assert first != later

    def test_full_loop_returns_to_start(self):
        # speed 0.5 → half a loop per second → back where it began after 2 s
        effect = RainbowEffect()
        start = effect.render(make_ctx(now=0.0, params={"speed": 0.5}))
        looped = effect.render(make_ctx(now=2.0, params={"speed": 0.5}))
        # float rounding may differ by 1 per channel after a full loop
        for before, after in zip(start, looped):
            assert abs(before.r - after.r) <= 1
            assert abs(before.g - after.g) <= 1
            assert abs(before.b - after.b) <= 1


class TestSpectrum:
    def test_whole_device_shares_one_color(self):
        frame = SpectrumEffect().render(make_ctx(leds=6, now=0.3))
        assert len(frame) == 6
        assert len(set(frame)) == 1

    def test_color_changes_over_time(self):
        effect = SpectrumEffect()
        assert effect.render(make_ctx(now=0.0)) != effect.render(make_ctx(now=0.5))


class TestWave:
    def test_band_is_bright_and_the_rest_is_dim(self):
        params = {"color": "#FF0000", "speed": 0.5}
        frame = WaveEffect().render(make_ctx(leds=20, now=0.0, params=params))
        assert len(frame) == 20
        reds = [color.r for color in frame]
        assert max(reds) > 200  # the lit band
        assert min(reds) < 40  # the dim base elsewhere
        assert min(reds) > 0  # …but never fully dark
        assert all(color.g == 0 and color.b == 0 for color in frame)

    def test_band_moves(self):
        effect = WaveEffect()
        params = {"color": "#00FF00", "speed": 0.5}
        first = effect.render(make_ctx(leds=20, now=0.0, params=params))
        later = effect.render(make_ctx(leds=20, now=0.5, params=params))
        brightest_first = max(range(20), key=lambda i: first[i].g)
        brightest_later = max(range(20), key=lambda i: later[i].g)
        assert brightest_first != brightest_later

    def test_uses_chosen_color(self):
        frame = WaveEffect().render(make_ctx(leds=1, params={"color": "#0000FF"}))
        assert frame[0].b > 0 and frame[0].r == 0 and frame[0].g == 0

    def test_out_of_range_params_degrade(self):
        # hand-edited storage must never crash a render
        frame = WaveEffect().render(
            make_ctx(leds=4, params={"color": "not a color", "speed": 99})
        )
        assert len(frame) == 4
        assert all(isinstance(color, RgbColor) for color in frame)


class TestCatalogSelectable:
    def test_reactive_effects_are_hidden_from_users(self):
        ids = [effect.effect_id for effect in EffectCatalog().selectable()]
        assert "reactive_temp" not in ids
        assert "reactive_alert" not in ids

    def test_builtins_listed_in_friendly_order(self):
        ids = [effect.effect_id for effect in EffectCatalog().selectable()]
        assert ids == ["static", "breathe", "rainbow", "spectrum", "wave"]

    def test_imported_effects_follow_builtins(self):
        from pulse_hwm.rgb.effects.declarative import DeclarativeEffect

        catalog = EffectCatalog()
        catalog.register(
            DeclarativeEffect(
                {"id": "user_glow", "name": "Glow", "params": {}, "layers": []}
            )
        )
        ids = [effect.effect_id for effect in catalog.selectable()]
        assert ids[-1] == "user_glow"
        # reactive effects still resolve for the planner
        assert catalog.get("reactive_temp") is not None
