"""Live LED preview: one row of pixel squares per device.

The RGB tab feeds it the exact frame the engine last handed to the driver
(as "#RRGGBB" strings from the worker's status report), so what you see
here is what Pulse is SENDING — every click gets visible confirmation even
before the hardware catches up.

Big per-key keyboards have ~100 LEDs; drawing each as a readable square
would overflow the row, so the frame is downsampled to at most
MAX_SQUARES evenly spaced LEDs. Hard-edged squares keep the pixel look.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from pulse_hwm.ui import theme as T

MAX_SQUARES = 64
SQUARE_GAP = 2
STRIP_HEIGHT = 16


def downsample(colors: list[str], limit: int = MAX_SQUARES) -> list[str]:
    """Pick at most `limit` evenly spaced entries (first and last kept) so a
    long frame still shows its overall pattern. Pure: unit-tested."""
    limit = max(2, int(limit))
    if len(colors) <= limit:
        return list(colors)
    step = (len(colors) - 1) / (limit - 1)
    return [colors[round(index * step)] for index in range(limit)]


class LedStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors: list[str] = []
        self._led_count = 0
        self.setMinimumHeight(STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(320, STRIP_HEIGHT)

    def set_colors(self, colors: list[str]) -> None:
        """New frame (possibly empty = device not driven by Pulse)."""
        colors = [str(c) for c in (colors or [])]
        if colors == self._colors:
            return  # a static color repeats every report; skip the repaint
        self._led_count = len(colors)
        self._colors = colors
        self.setToolTip(f"{self._led_count} LEDs" if colors else "not driven by Pulse")
        self.update()

    def colors(self) -> list[str]:
        return list(self._colors)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        shown = downsample(self._colors) if self._colors else []
        # an undriven device shows a row of dark outlined squares so the
        # row keeps its shape instead of disappearing
        count = len(shown) or 16
        size = max(
            4, min(STRIP_HEIGHT, (self.width() + SQUARE_GAP) // count - SQUARE_GAP)
        )
        top = (self.height() - size) // 2
        for index in range(count):
            x = index * (size + SQUARE_GAP)
            if shown:
                painter.fillRect(x, top, size, size, QColor(shown[index]))
            else:
                painter.setPen(QPen(QColor(T.LINE), 1))
                painter.drawRect(x, top, size - 1, size - 1)
        painter.end()
