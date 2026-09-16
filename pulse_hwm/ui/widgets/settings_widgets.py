"""Settings building blocks for the M6 rework — pixel-aesthetic widgets.

Three pieces, each tiny and purpose-built:
  FormRow             one settings row: ALL-CAPS label + control, aligned
                      and consistently spaced; optional help line
  CollapsiblePanel    a PixelPanel whose title row toggles the body — the
                      [−]/[+] glyph is flat-mode, pixel-font, and the
                      collapsed state persists per section id via the
                      standard settings DB (no new storage)
  SettingsRail        the LEFT rail: section buttons stacked vertically
                      bound 1:1 to pages of one QStackedWidget host

Rendering notes:
  * Collapsible shapes use hard-edged text glyphs ([−]/[+]) instead of
    arrows — reads as terminal/framebuffer style, consistent with the app.
  * Rail buttons are custom-painted QPushButtons styled by theme.qss (#panel
    classes); the ACTIVE page marker is a plain accent left border.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm.db import Database
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel


class FormRow(QWidget):
    """One settings row: ALL-CAPS label + control, optional help line."""

    def __init__(self, label: str, control, help_text: str = "", parent=None):
        super().__init__(parent)
        self._control = control
        self._help_text = help_text
        self._help_label: QLabel | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(1)
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(label.upper())
        label.setObjectName("panelTitle")
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(control)
        layout.addLayout(row)
        if help_text:
            self._help_label = QLabel(help_text)
            self._help_label.setObjectName("muted")
            self._help_label.setWordWrap(True)
            layout.addWidget(self._help_label)

    @property
    def control(self):
        return self._control

    def help(self) -> QLabel | None:
        return self._help_label


class CollapsiblePanel(PixelPanel):
    """PixelPanel wrapper with a [−]/[+] toggle in the title row.

    The nested body widget is created by callers via body() BEFORE the
    toggle hides it — standard usage:
        panel = CollapsiblePanel("ALERTS", db)
        panel.body().addLayout(form)
    """

    def __init__(
        self, title: str, db: Database, section_key: str | None = None, parent=None
    ):
        super().__init__(title, parent)
        self._db = db
        self._state_key = f"settings_collapsed::{section_key or title.lower()}"
        restored = self._load_state()
        self._collapsed = restored == "1"

        if self._title is not None:
            # take the built-in title label and pair it with the toggle
            self._title.setFixedHeight(18)
            self._title.setCursor(Qt.CursorShape.PointingHandCursor)
            self._title.mousePressEvent = lambda event: self.toggle()  # type: ignore[assignment]
        self._toggle = QPushButton(("[-]" if not self._collapsed else "[+]"))
        self._toggle.setObjectName("muted")
        self._toggle.setFlat(True)
        self._toggle.setFixedHeight(18)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.clicked.connect(self.toggle)
        header = self._layout.itemAt(0)
        if isinstance(header, QLabel) and header is self._title:
            header_row = QHBoxLayout()
            header_row.addWidget(self._title, 1)
            header_row.addWidget(self._toggle, 0)
            container = QWidget()
            container.setLayout(header_row)
            self._layout.insertWidget(0, container)
        self.apply_collapsed(self._collapsed)

    # ── behavior ────────────────────────────────────────────────────────
    def toggle(self) -> None:
        self.apply_collapsed(not self._collapsed)

    def apply_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self._toggle.setText("[+]" if collapsed else "[-]")
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item else None
            if widget is None or widget is self._toggle:
                continue
            if hasattr(widget, "isHidden") and widget is not self._title:
                widget.setHidden(collapsed)
        try:
            self._db.set_setting(self._state_key, "1" if collapsed else "0")
        except Exception:
            pass  # state persistence is best-effort; visuals stay correct

    def _load_state(self) -> str:
        try:
            return str(self._db.get_setting(self._state_key, ""))
        except Exception:
            return ""


class SettingsRail(QWidget):
    """Vertical section rail + stacked pages. Load sections in order; each
    section maps to exactly one page widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._sections_column = QVBoxLayout()
        self._buttons: list[QPushButton] = []
        rail_host = QWidget()
        rail_host.setLayout(self._sections_column)
        rail_host.setFixedWidth(150)
        layout.addWidget(rail_host, 0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        self._pages: list[QWidget] = []

    # ── wiring ──────────────────────────────────────────────────────────
    def add_section(self, label: str, page: QWidget) -> None:
        button = QPushButton(label.upper())
        button.setObjectName("railButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        index = self._sections_column.count()
        button.clicked.connect(lambda _checked, i=index: self.select(i))
        self._buttons.append(button)
        self._sections_column.addWidget(button)
        self._stack.addWidget(page)
        if index == 0:
            self.select(0)

    def add_stretch(self) -> None:
        self._sections_column.addStretch(1)

    def select(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        for i, button in enumerate(self._buttons):
            button.setChecked(i == index)

    def active_section(self) -> int:
        return self._stack.currentIndex()
