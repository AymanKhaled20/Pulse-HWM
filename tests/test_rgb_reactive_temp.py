"""ReactiveTempEffect + engine sensor plumbing tests."""

from __future__ import annotations

from pulse_hwm.rgb.effects.base import EffectContext
from pulse_hwm.rgb.effects.builtin import ReactiveTempEffect, SensorMissingColor
from pulse_hwm.rgb.model import LedLayout, RgbColor, RgbDevice


def device():
    return RgbDevice(
        device_id="d",
        name="D",
        driver_id="fake",
        leds=6,
        layout=LedLayout(led_count=6),
    )


def ctx(temperature: float | None):
    return EffectContext(
        device=device(),
        sensors={"cpu_temp": temperature},
    )


def test_cool_maps_to_low_color():
    effect = ReactiveTempEffect()
    frame = effect.render(ctx(40.0))  # default low = 40
    assert all(c == RgbColor(0, 255, 65) for c in frame)


def test_hot_maps_to_high_color():
    effect = ReactiveTempEffect()
    frame = effect.render(ctx(85.0))  # default high = 85
    assert all(c == RgbColor(255, 59, 48) for c in frame)


def test_midpoint_interpolates():
    effect = ReactiveTempEffect()
    mid = effect.render(ctx(62.5))[0]  # halfway 40..85
    # halfway between green(0,255,65) and red(255,59,48) with clamping
    assert 110 < mid.r < 150
    assert 140 < mid.g < 190


def test_clamped_extremes():
    effect = ReactiveTempEffect()
    below = effect.render(ctx(-10.0))[0]
    above = effect.render(ctx(200.0))[0]
    assert below == RgbColor(0, 255, 65)
    assert above == RgbColor(255, 59, 48)


def test_missing_sensor_gets_dim_marker_not_black():
    effect = ReactiveTempEffect()
    frame = effect.render(ctx(None))
    assert all(c == SensorMissingColor() for c in frame)


def test_frame_size_matches_device():
    effect = ReactiveTempEffect()
    assert len(effect.render(ctx(50.0))) == 6


def test_catalog_registry_contains_reactive_temp():
    from pulse_hwm.rgb.effects.catalog import EffectCatalog

    catalog = EffectCatalog()
    assert catalog.get("reactive_temp") is not None
    assert "reactive_temp" in catalog.ids()
