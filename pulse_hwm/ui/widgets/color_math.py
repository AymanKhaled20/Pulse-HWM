"""Color math for the pixel picker — Qt-free, fully unit-testable.

HSV over 0..1 channels; integer-hex conversion happens at the widget edge
via the shared RgbColor model. Round-trips rgb→hsv→rgb within ±1 per
channel (8-bit rounding), which the tests pin exactly.
"""

from __future__ import annotations

from pulse_hwm.rgb.model import RgbColor


def rgb_to_hsv(color: RgbColor) -> tuple[float, float, float]:
    """(h 0..1, s 0..1, v 0..1). Black is h=0, s=0, v=0; gray rows keep
    h=0, matching the picker's leftmost-column behavior."""
    r = color.r / 255.0
    g = color.g / 255.0
    b = color.b / 255.0
    maximum = max(r, g, b)
    minimum = min(r, g, b)
    delta = maximum - minimum
    if delta == 0:
        hue = 0.0
    elif maximum == r:
        hue = ((g - b) / delta) / 6.0 % 1.0
    elif maximum == g:
        hue = (((b - r) / delta) + 2.0) / 6.0
    else:
        hue = (((r - g) / delta) + 4.0) / 6.0
    saturation = 0.0 if maximum == 0 else delta / maximum
    return (hue, saturation, maximum)


def hsv_to_rgb(hue: float, saturation: float, value: float) -> RgbColor:
    """Clamps all channels; h wraps. Deterministic on 0..1 inputs."""
    hue = max(0.0, min(1.0, float(hue))) % 1.0
    saturation = max(0.0, min(1.0, float(saturation)))
    value = max(0.0, min(1.0, float(value)))
    section = int(hue * 6.0)
    fraction = hue * 6.0 - section
    p = value * (1.0 - saturation)
    q = value * (1.0 - fraction * saturation)
    t = value * (1.0 - (1.0 - fraction) * saturation)
    channels = {
        0: (value, t, p),
        1: (q, value, p),
        2: (p, value, t),
        3: (p, q, value),
        4: (t, p, value),
        5: (value, p, q),
    }
    r, g, b = channels[section % 6]
    return RgbColor(round(r * 255), round(g * 255), round(b * 255))
