"""RgbColor / RgbEffect / layout model tests — pure, no Qt, no I/O."""

from __future__ import annotations

import pytest

from pulse_hwm.rgb.layout import aula_f75_layout, grid_layout, strip_layout
from pulse_hwm.rgb.model import (
    MODE_STATIC,
    LedLayout,
    RgbColor,
    RgbDevice,
    RgbEffect,
    solid_frame,
)


class TestRgbColor:
    def test_hex_round_trip(self):
        c = RgbColor.from_hex("#FFD400")
        assert (c.r, c.g, c.b) == (255, 212, 0)
        assert c.to_hex() == "#FFD400"

    def test_hex_without_hash(self):
        assert RgbColor.from_hex("00FF41") == RgbColor(0, 255, 65)

    def test_garbage_falls_back_to_black(self):
        # settings storage is user-editable: malformed values must degrade
        assert RgbColor.from_hex("nope") == RgbColor(0, 0, 0)
        assert RgbColor.from_hex("#12345") == RgbColor(0, 0, 0)
        assert RgbColor.from_hex("") == RgbColor(0, 0, 0)
        assert RgbColor.from_hex(None) == RgbColor(0, 0, 0)  # type: ignore[arg-type]

    def test_channels_clamped(self):
        c = RgbColor(300, -5, 128)
        assert (c.r, c.g, c.b) == (255, 0, 128)

    def test_scaled_brightness(self):
        c = RgbColor(100, 200, 50)
        assert c.scaled(0.5) == RgbColor(50, 100, 25)
        assert c.scaled(2.0) == c  # factor clamped to 1.0
        assert c.scaled(-1) == RgbColor(0, 0, 0)


class TestRgbEffect:
    def test_defaults_are_valid(self):
        e = RgbEffect()
        assert e.mode == MODE_STATIC
        assert 0 <= e.brightness <= 100
        assert 0 <= e.speed <= 100

    def test_ranges_clamped(self):
        e = RgbEffect(brightness=250, speed=-10)
        assert e.brightness == 100
        assert e.speed == 0


class TestLedLayout:
    def test_key_names_length_enforced(self):
        with pytest.raises(ValueError):
            LedLayout(led_count=3, key_names=("A", "B"))

    def test_positions_length_enforced(self):
        with pytest.raises(ValueError):
            LedLayout(led_count=3, positions=((0.0, 0.0),))

    def test_valid_layout_passes(self):
        layout = LedLayout(led_count=2, positions=((0.0, 0.0), (1.0, 1.0)))
        assert layout.led_count == 2


class TestLayoutBuilders:
    def test_aula_f75_layout_shape(self):
        layout = aula_f75_layout()
        assert layout.matrix is not None
        rows = layout.matrix[0]
        assert rows == 5
        assert layout.led_count == len(layout.key_names or ())
        assert layout.led_count == len(layout.positions)

    def test_aula_f75_positions_normalized(self):
        layout = aula_f75_layout()
        for x, y in layout.positions:
            assert 0.0 <= x <= 1.0
            assert 0.0 < y <= 1.0

    def test_aula_f75_space_is_widest_key(self):
        layout = aula_f75_layout()
        names = layout.key_names or ()
        idx = names.index("SPACE")
        assert idx > 0
        # SPACE sits left-of-center-ish on a 65% board's bottom row
        x = layout.positions[idx][0]
        assert 0.2 < x < 0.6

    def test_grid_layout(self):
        layout = grid_layout(2, 4)
        assert layout.led_count == 8
        assert layout.matrix == (2, 4)
        assert len(layout.positions) == 8

    def test_strip_layout(self):
        assert strip_layout(12).led_count == 12
        assert strip_layout(12, vertical=True).matrix == (12, 1)


class TestRgbDeviceAndFrames:
    def test_solid_frame_matches_led_count(self):
        device = RgbDevice(device_id="d1", name="Test", driver_id="fake", leds=6)
        frame = solid_frame(device, RgbColor(1, 2, 3))
        assert len(frame) == 6
        assert all(c == RgbColor(1, 2, 3) for c in frame)

    def test_solid_frame_min_one_led(self):
        # leds == 0 (unknown) must still produce a usable 1-LED frame
        device = RgbDevice(device_id="d1", name="Test", driver_id="fake", leds=0)
        assert len(solid_frame(device, RgbColor())) == 1
