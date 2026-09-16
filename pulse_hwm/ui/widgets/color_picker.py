"""Pixel-art HSV color picker.

Two custom-painted regions:
  * SAT-VALUE SQUARE (left): x = saturation, y = value; the crosshair is the
    current selection. Fully custom painting keeps the CRT/pixel aesthetic
    (square blocks, hard edges, no anti-aliasing) rather than a stock
    QColorDialog which would clash with the theme.
  * HUE STRIP (right): seven pixel segments cycling the hue wheel.

Emits `hex_chosen(str)` — the RGB tab saves `#RRGGBB` via the settings
instant-apply contract. Reads theme globals at paint time so theme switches
recolor it like every other custom widget.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from pulse_hwm.rgb.model import RgbColor
from pulse_hwm.ui import theme as T
from pulse_hwm.ui.widgets.color_math import hsv_to_rgb, rgb_to_hsv

HUE_STRIP_WIDTH = 26
HUE_SEGMENTS = 12


class ColorPicker(QWidget):
    hex_chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 132)
        self._hue = 0.12  # start near the Pulse amber
        self._sat = 1.0
        self._value = 1.0

    # ── state ───────────────────────────────────────────────────────────
    def set_hex(self, value: str) -> None:
        """External set (settings load): update WITHOUT emitting."""
        rgb = RgbColor.from_hex(value)
        self._hue, self._sat, self._value = rgb_to_hsv(rgb)
        self.update()

    def hex(self) -> str:
        return self._rgb().to_hex()

    def _rgb(self) -> RgbColor:
        return hsv_to_rgb(self._hue, self._sat, self._value)

    # ── interaction ─────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        self._handle(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons():
            self._handle(event)

    def _handle(self, event) -> None:
        position = event.position()
        square_width = self.width() - HUE_STRIP_WIDTH - 10
        in_square = self._point_in_square(position.x(), square_width)
        if in_square:
            self._sat = min(1.0, max(0.0, position.x() / max(1.0, square_width - 6)))
            self._value = min(
                1.0, max(0.0, 1.0 - (position.y() - 6) / max(1.0, self.height() - 12))
            )
        else:
            # hue strip: y position → hue segment
            y = min(max(0.0, position.y() - 6), self.height() - 12)
            self._hue = (y / max(1.0, self.height() - 12)) % 1.0
        self.update()
        self.hex_chosen.emit(self.hex())

    def _point_in_square(self, x: float, square_width: float) -> bool:
        return x <= square_width

    # ── painting ────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(T.PANEL))
        square_width = self.width() - HUE_STRIP_WIDTH - 10
        self._paint_sat_value_square(painter, square_width)
        self._paint_hue_strip(painter)
        self._paint_cursor(painter, square_width)
        painter.end()

    def _paint_sat_value_square(self, painter: QPainter, square_width: int) -> None:
        rows = 10
        columns = 10
        cell_w = max(1, (square_width - 12) // columns)
        cell_h = max(1, (self.height() - 12) // rows)
        for row in range(rows):  # y: top = full value
            for column in range(columns):  # x: left = no sat
                value = 1.0 - row / max(1.0, rows - 1)
                sat = column / max(1.0, columns - 1)
                rgb = hsv_to_rgb(self._hue, sat, value)
                painter.fillRect(
                    6 + column * cell_w,
                    6 + row * cell_h,
                    cell_w,
                    cell_h,
                    QColor(rgb.r, rgb.g, rgb.b),
                )
        painter.setPen(QPen(QColor(T.LINE), 1))
        painter.drawRect(5, 5, square_width - 11, self.height() - 11)

    def _paint_hue_strip(self, painter: QPainter) -> None:
        strip_x = self.width() - HUE_STRIP_WIDTH + 2
        cell_h = max(1, (self.height() - 12) // HUE_SEGMENTS)
        for segment in range(HUE_SEGMENTS):
            hue = segment / HUE_SEGMENTS
            rgb = hsv_to_rgb(hue, 1.0, 1.0)
            painter.fillRect(
                strip_x,
                6 + segment * cell_h,
                HUE_STRIP_WIDTH - 4,
                cell_h,
                QColor(rgb.r, rgb.g, rgb.b),
            )
        painter.setPen(QPen(QColor(T.LINE), 1))
        painter.drawRect(strip_x - 1, 5, HUE_STRIP_WIDTH - 2, self.height() - 11)

    def _paint_cursor(self, painter: QPainter, square_width: int) -> None:
        cursor_x = 6 + int(self._sat * (square_width - 12))
        cursor_y = 6 + int((1.0 - self._value) * (self.height() - 12))
        painter.setPen(QPen(QColor(T.PRIMARY), 2))
        painter.drawRect(cursor_x - 3, cursor_y - 3, 6, 6)
        # hue marker: ring drawn on the strip at the current hue
        strip_x = self.width() - HUE_STRIP_WIDTH
        hue_y = 6 + int(self._hue * (self.height() - 12))
        painter.drawRect(strip_x - 2, hue_y - 3, HUE_STRIP_WIDTH - 6, 6)
