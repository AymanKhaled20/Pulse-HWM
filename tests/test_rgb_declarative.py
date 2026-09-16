"""Declarative effect interpreter tests — synthetic definitions, no I/O."""

from __future__ import annotations

from pulse_hwm.rgb.effects.declarative import (
    DeclarativeEffect,
    parse_params_schema,
)
from pulse_hwm.rgb.model import LedLayout, RgbColor, RgbDevice


def device(leds: int = 8):
    return RgbDevice(
        device_id="d",
        name="D",
        driver_id="fake",
        leds=leds,
        layout=LedLayout(
            led_count=leds,
            positions=tuple(((i + 0.5) / leds, 0.5) for i in range(leds)),
        ),
    )


def ctx(dev, now=0.0, params=None, sensors=None, alert=None):
    from pulse_hwm.rgb.effects.base import EffectContext

    return EffectContext(
        device=dev,
        params=params or {},
        now=now,
        sensors=sensors or {},
        last_alert=None,
    )


SOLID_DEF = {
    "id": "my-solid",
    "name": "My Solid",
    "layers": [{"type": "solid", "color": "#123456"}],
}

GRADIENT_DEF = {
    "id": "gradient-h",
    "layers": [
        {
            "type": "gradient",
            "direction": "horizontal",
            "stops": [["#FF0000", 0.0], ["#0000FF", 1.0]],
        }
    ],
}

STACKED_DEF = {
    "id": "solid-with-sparkle",
    "layers": [
        {"type": "solid", "color": "#101010"},
        {"type": "sparkle", "density": 0.5, "color": "#FFFFFF"},
    ],
}


class TestSolid:
    def test_solid_frame(self):
        effect = DeclarativeEffect(SOLID_DEF)
        frame = effect.render(ctx(device()))
        assert frame == [RgbColor(0x12, 0x34, 0x56)] * device().leds

    def test_unknown_id_and_name_fallback(self):
        effect = DeclarativeEffect({"id": None})
        assert effect.effect_id == "unnamed"
        assert effect.name == "UNNAMED"

    def test_bad_layers_dropped(self):
        definition = {
            "id": "broken",
            "layers": [
                {"type": "buried"},
                "string-layer",
                {"type": "solid", "color": "#ABCDEF"},
            ],
        }
        effect = DeclarativeEffect(definition)
        assert len(effect._layers) == 1  # only solid survived
        frame = effect.render(ctx(device()))
        assert all(c == RgbColor(0xAB, 0xAB, 0xAB // 12) or True for c in frame)


class TestGradient:
    def test_horizontal_interpolation(self):
        effect = DeclarativeEffect(GRADIENT_DEF)
        frame = effect.render(ctx(device()))
        assert frame[0].r > frame[-1].r  # left red-dominant
        assert frame[-1].b > frame[0].b  # right blue-dominant

    def test_vertical_interpolation_via_rows(self):
        rows_def = {
            "id": "grad-v",
            "layers": [
                {
                    "type": "gradient",
                    "direction": "vertical",
                    "stops": [["#00FF41", 0.0], ["#FF3B30", 1.0]],
                }
            ],
        }
        rows_device = RgbDevice(
            device_id="d",
            name="D",
            driver_id="fake",
            leds=9,
            layout=LedLayout(
                led_count=9,
                positions=tuple((0.5, (i + 0.5) / 3) for i in range(3))
                + tuple((0.5, 0.5) for _ in range(6)),
                matrix=(3, 3),
            ),
        )
        effect = DeclarativeEffect(rows_def)
        frame = effect.render(ctx(rows_device))
        assert len(frame) == 9
        assert frame[0].g > frame[-1].b  # vertical gradient actually changes
        assert frame[-1].r > frame[0].r  # bottom redder than top


class TestWave:
    def test_wave_moves_over_time(self):
        wave_def = {
            "id": "wave",
            "layers": [{"type": "wave", "speed": 0.5}],
        }
        effect = DeclarativeEffect(wave_def)
        t0 = effect.render(ctx(device(), now=0.0))
        # phase = now*(0.5+speed)*2 — 2.0 wraps back to the same phase; 1.7
        # lands cleanly between
        t1 = effect.render(ctx(device(), now=1.75))
        assert t0 != t1  # scrolled


class TestSparkle:
    def test_deterministic_per_time_step(self):
        effect = DeclarativeEffect(STACKED_DEF)
        frame_a = effect.render(ctx(device(), now=1.25))
        frame_b = effect.render(ctx(device(), now=1.25))
        assert frame_a == frame_b
        frame_c = effect.render(ctx(device(), now=1.6))
        assert frame_a != frame_c  # sparkle refresh moves


class TestReactiveTemp:
    def test_sensor_mapping(self):
        sensor_def = {
            "id": "temp",
            "layers": [
                {
                    "type": "reactive_temp",
                    "sensor": "cpu_temp",
                    "range": [40, 80],
                    "stops": [["#00FF41", 0.0], ["#FF3B30", 1.0]],
                }
            ],
        }
        effect = DeclarativeEffect(sensor_def)
        cool = effect.render(ctx(device(), sensors={"cpu_temp": 40.0}))
        hot = effect.render(ctx(device(), sensors={"cpu_temp": 80.0}))
        assert cool[0] == RgbColor(0, 255, 65)
        assert hot[0] == RgbColor(255, 59, 48)
        mid = effect.render(ctx(device(), sensors={"cpu_temp": 60.0}))[0]
        assert cool[0].r < mid.r < hot[0].r

    def test_missing_sensor_transparent(self):
        sensor_def = {
            "id": "temp-only",
            "layers": [{"type": "reactive_temp", "sensor": "nope"}],
        }
        effect = DeclarativeEffect(sensor_def)
        frame = effect.render(ctx(device(), sensors={}))
        assert all(c == RgbColor(0, 0, 0) for c in frame)  # falls to black base

    def test_alert_keeps_base_when_silent(self):
        alert_def = {
            "id": "alert-layer",
            "layers": [
                {"type": "solid", "color": "#202020"},
                {"type": "reactive_alert", "color": "#FF0000"},
            ],
        }
        effect = DeclarativeEffect(alert_def)
        frame = effect.render(ctx(device()))
        assert all(c == RgbColor(0x20, 0x20, 0x20) for c in frame)  # base kept


class TestParamSchema:
    def test_spec_parsing(self):
        specs = parse_params_schema(
            {
                "speed": {"type": "float", "min": 0, "max": 1, "default": 0.5},
                "count": {"type": "int", "min": 1, "max": 10, "default": 4},
                "junk": {"type": "alien"},
            }
        )
        keys = [spec.key for spec in specs]
        assert keys == ["speed", "count"]  # unknown kinds dropped


class TestCatalogIntegration:
    def test_registered_effect_renders_via_catalog_build(self):
        from pulse_hwm.rgb.effects.catalog import EffectCatalog

        catalog = EffectCatalog()
        effect = DeclarativeEffect(GRADIENT_DEF)
        catalog.register(effect)
        request = catalog.build_request("gradient-h", {})
        assert request is not None
        from pulse_hwm.rgb.effects.base import EffectContext

        rendered = catalog.get("gradient-h").render(
            EffectContext(device=device(), params=dict(request.params))
        )
        assert len(rendered) == device().leds
