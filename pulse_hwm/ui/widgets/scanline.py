from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class ScanlineOverlay(QWidget):
    """CRT-style horizontal scanlines. Purely decorative; ignores mouse."""

    SPACING = 3
    ALPHA = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setPen(QPen(QColor(0, 0, 0, self.ALPHA), 1))
        y = 0
        height = self.height()
        width = self.width()
        while y < height:
            p.drawLine(0, y, width, y)
            y += self.SPACING
        p.end()
