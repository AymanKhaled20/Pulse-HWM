from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from pulse_hwm.ui import theme as T

SEGMENTS = 20
SEG_GAP = 1


class GaugeBar(QWidget):
    """Horizontal segmented bar, pixel style. Amber → red past 85%."""

    HIGH = 0.85
    MID = 0.60

    def __init__(self, segments: int = SEGMENTS, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._segments = segments
        self.setFixedHeight(14)
        self.setMinimumWidth(80)

    def set_value(self, pct: float) -> None:
        self._value = max(0.0, min(1.0, pct))
        self.update()

    def value(self) -> float:
        return self._value

    def _color(self, i: int) -> str:
        frac = (i + 1) / self._segments
        if frac >= self.HIGH:
            return T.DANGER
        if frac >= self.MID:
            return T.HIGHLIGHT
        return T.PRIMARY

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor(T.PANEL_ALT))
        w, h = self.width(), self.height()
        seg_w = (w - (self._segments - 1)) / self._segments
        filled = round(self._value * self._segments)
        for i in range(self._segments):
            x = i * (seg_w + 1)
            rect = QRectF(x, 0, seg_w, h)
            if i < filled:
                p.fillRect(rect, QColor(self._color(i)))
            else:
                p.fillRect(rect, QColor(T.PANEL))
        p.setPen(QPen(QColor(T.LINE), 1))
        p.drawRect(QRectF(0, 0, w - 1, h - 1))
        p.end()


class CpuCoreGrid(QWidget):
    """One column of pixel segments per CPU core."""

    def __init__(self, cores: list[float] | None = None, parent=None):
        super().__init__(parent)
        self._values: list[float] = cores or []
        self.setMinimumHeight(44)

    def set_values(self, values: list[float]) -> None:
        self._values = list(values)
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor(T.PANEL_ALT))
        w, h = self.width(), self.height()
        n = max(1, len(self._values))
        col_w = (w - (n - 1) * 2) / n
        rows = 6
        for i, val in enumerate(self._values):
            x = i * (col_w + 2)
            filled = round(min(1.0, max(0.0, val / 100.0)) * rows)
            row_h = h / rows
            for r in range(rows):
                y = h - (r + 1) * row_h
                rect = QRectF(x, y, col_w, max(1.0, row_h - 1))
                if r < filled:
                    color = T.DANGER if r >= rows - 1 else T.PRIMARY if r < rows - 3 else T.HIGHLIGHT
                    p.fillRect(rect, QColor(color))
                else:
                    p.fillRect(rect, QColor(T.PANEL_ALT))
            p.setPen(QPen(QColor(T.LINE), 1))
            p.drawRect(QRectF(x, 0, col_w, h - 1))
        if not self._values:
            p.setPen(QPen(QColor(T.MUTED), 1))
            p.drawRect(QRectF(0, 0, w - 1, h - 1))
        p.end()


class Led(QWidget):
    """Blinking status LED."""

    BLINK_MS = 600

    def __init__(self, color: str = T.SUCCESS, on: bool = True, parent=None):
        super().__init__(parent)
        self._color = color
        self._on = on
        self.setFixedSize(14, 14)
        self._blink_timer = None
        self._blink_on = True

    def set_state(self, on: bool, color: str | None = None) -> None:
        self._on = on
        if color:
            self._color = color
        self.update()

    def start_blink(self) -> None:
        self._blink_on = True
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor(T.PANEL))
        if self._on:
            p.fillRect(2, 2, self.width() - 4, self.height() - 4, QColor(self._color))
            p.fillRect(2, 2, (self.width() - 4) // 2, (self.height() - 4) // 5, QColor(T.HIGHLIGHT))
        else:
            p.fillRect(2, 2, self.width() - 4, self.height() - 4, QColor(T.LINE))
        p.setPen(QPen(QColor(T.LINE), 1))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        p.end()


class StatRow(QWidget):
    """Label + dotted fill + value, one line."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout, QLabel
        self._label = QLabel(label)
        self._label.setObjectName("muted")
        self._value = QLabel("—")
        self._value.setObjectName("stat")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._label)
        row.addStretch(1)
        row.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)
