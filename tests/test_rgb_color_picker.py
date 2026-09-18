"""Color math tests (Qt-free) + offscreen picker smoke tests."""

from __future__ import annotations

from pulse_hwm.rgb.model import RgbColor
from pulse_hwm.ui.widgets.color_math import hsv_to_rgb, rgb_to_hsv


def test_primary_hues_map_to_expected_rgb():
    assert hsv_to_rgb(0.0, 1.0, 1.0) == RgbColor(255, 0, 0)  # red
    assert hsv_to_rgb(2 / 6, 1.0, 1.0) == RgbColor(0, 255, 0)  # green
    assert hsv_to_rgb(4 / 6, 1.0, 1.0) == RgbColor(0, 0, 255)  # blue


def test_value_only_is_white_and_black():
    assert hsv_to_rgb(0.5, 0.0, 1.0) == RgbColor(255, 255, 255)
    assert hsv_to_rgb(0.5, 0.0, 0.0) == RgbColor(0, 0, 0)


def test_yellow_and_cyan_and_magenta():
    assert hsv_to_rgb(1 / 6, 1.0, 1.0) == RgbColor(255, 255, 0)
    assert hsv_to_rgb(3 / 6, 1.0, 1.0) == RgbColor(0, 255, 255)
    assert hsv_to_rgb(5 / 6, 1.0, 1.0) == RgbColor(255, 0, 255)


def test_channels_clamped():
    assert hsv_to_rgb(2.5, 5.0, -1.0) == RgbColor(0, 0, 0)
    assert hsv_to_rgb(-3, 0.5, 0.5).r >= 0


def test_round_trip_tolerance_one():
    for hex_value in ("#FFD400", "#00FF41", "#FF3B30", "#7A01B2", "#123456"):
        rgb = RgbColor.from_hex(hex_value)
        back = hsv_to_rgb(*rgb_to_hsv(rgb))
        assert abs(back.r - rgb.r) <= 1
        assert abs(back.g - rgb.g) <= 1
        assert abs(back.b - rgb.b) <= 1


def test_grays_have_zero_saturation():
    for hex_value in ("#000000", "#808080", "#FFFFFF"):
        hue, saturation, _ = rgb_to_hsv(RgbColor.from_hex(hex_value))
        assert saturation < 1e-6
        assert hue == 0.0


def test_pure_hues_decode():
    hue, _, _ = rgb_to_hsv(RgbColor.from_hex("#FF0000"))
    assert abs(hue - 0.0) < 1e-6 or abs(hue - 1.0) < 1e-6
    hue, _, _ = rgb_to_hsv(RgbColor.from_hex("#00FF00"))
    assert abs(hue - 2 / 6) < 1e-6
    hue, _, _ = rgb_to_hsv(RgbColor.from_hex("#0000FF"))
    assert abs(hue - 4 / 6) < 1e-6


class _PositionShim:
    def __init__(self, x: float, y: float):
        self._x, self._y = x, y

    def x(self) -> float:
        return self._x

    def y(self) -> float:
        return self._y


class _EventShim:
    """Minimal stand-in for QMouseEvent: position() + optional buttons()."""

    def __init__(self, x: float, y: float, pressed: bool = False):
        self._x, self._y = x, y
        self._pressed = pressed

    def position(self) -> _PositionShim:
        return _PositionShim(self._x, self._y)

    def buttons(self) -> int:
        return 1 if self._pressed else 0


def new_picker():
    from PySide6.QtWidgets import QApplication

    from pulse_hwm.ui.widgets.color_picker import ColorPicker

    app = QApplication.instance() or QApplication([])
    picker = ColorPicker()
    picker.set_hex("#00FF41")
    return picker, app


def test_set_hex_updates_state_silently():
    picker, _app = new_picker()
    received = []
    picker.hex_chosen.connect(received.append)
    picker.set_hex("#FF0000")
    assert picker.hex() == "#FF0000"
    assert received == []  # external set must NOT emit


def test_drag_on_square_emits_chosen():
    picker, _app = new_picker()
    received = []
    picker.hex_chosen.connect(received.append)
    picker._handle(_EventShim(20.0, 20.0))  # inside sat-value square
    assert received, "interaction must emit hex_chosen"
    assert picker.hex() != "#00FF41"  # selection actually moved


def test_drag_on_hue_strip_changes_hue():
    picker, _app = new_picker()
    picker.set_hex("#FFD400")
    before = picker._hue
    # far right = beyond the square = the hue strip
    picker._handle(_EventShim(215.0, 90.0))
    assert abs(picker._hue - before) > 1e-6
