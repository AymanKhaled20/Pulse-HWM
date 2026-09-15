from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class PixelPanel(QFrame):
    """Bordered panel with optional title. objectName=pixelPanel for QSS."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("pixelPanel")
        self._layout = QVBoxLayout(self)
        # compact margins keep the minimum panel height low so the
        # dashboard fits inside short (laptop) windows without clipping
        self._layout.setContentsMargins(10, 6, 10, 8)
        self._layout.setSpacing(4)
        self._title = None
        if title:
            self._title = QLabel(title.upper())
            self._title.setObjectName("panelTitle")
            self._layout.addWidget(self._title)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add_body(self, widget) -> None:
        self._layout.addWidget(widget)


class StdoutPlaceholder(QWidget):
    """Phase placeholder — replaced as collectors land."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        label = QLabel(text)
        label.setObjectName("muted")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addWidget(label)
