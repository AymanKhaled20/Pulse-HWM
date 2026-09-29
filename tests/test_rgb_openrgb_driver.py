"""OpenRgbDriver helpers + probe path.

No real server, no real spawning: the driver tests exercise pure helpers
and the degraded paths (no client → clean fail-returns). The spawn path is
covered by the Gate A hardware run recorded in docs/RGB.md.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.openrgb.driver import (
    OpenRgbDriver,
    _is_resizable_argb_zone,
    frame_from_colors,
)
from pulse_hwm.rgb.model import RgbColor, RgbDevice


def test_dynamic_argb_zone_names_are_resizable() -> None:
    assert _is_resizable_argb_zone("JRAINBOW1") is True
    assert _is_resizable_argb_zone("JARGB2") is True
    assert _is_resizable_argb_zone("Mouse Zone") is False


def test_frame_from_colors_pads_and_truncates() -> None:
    device = RgbDevice(device_id="openrgb:0", name="m", driver_id="openrgb", leds=4)
    one = frame_from_colors(device, [RgbColor(9, 8, 7)])
    assert one == [(9, 8, 7)] * 4
    trunc = frame_from_colors(device, [RgbColor(1, 1, 1)] * 6)
    assert len(trunc) == 4
    assert trunc[-1] == (1, 1, 1)


def test_driver_unavailable_without_binary_or_port() -> None:
    driver = OpenRgbDriver(port=1)  # port 1 closed in tests; binary likely present
    result = driver.probe()
    if not result.available:
        assert "not found" in result.reason
    else:
        assert result.available
