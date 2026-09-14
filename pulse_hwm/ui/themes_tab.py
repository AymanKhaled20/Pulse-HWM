from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm.ui import theme as T
from pulse_hwm.ui.palettes import COLOR_THEMES, FONT_THEMES
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel


class _ColorCard(QWidget):
    """Clickable preview of one color theme: palette chips + name.

    Fully custom-painted (not QSS) so it can show colors from ITS OWN
    palette rather than whatever is currently active.
    """

    chosen = Signal(str)

    def __init__(self, color_theme, parent=None):
        super().__init__(parent)
        self._ct = color_theme
        self._active = False  # resolved once by ThemesTab._refresh_active()
        self.setFixedHeight(62)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, on: bool) -> None:
        if self._active != on:
            self._active = on
            self.update()

    def mousePressEvent(self, ev) -> None:
        self.chosen.emit(self._ct.id)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.PANEL))
        p.setPen(
            QPen(
                QColor(T.PRIMARY if self._active else T.LINE), 2 if self._active else 1
            )
        )
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        # palette chips in fixed order so cards are visually comparable
        chip_w = max(10, (self.width() - 20) // 7)
        for i, field in enumerate(
            ("bg", "panel_alt", "line", "primary", "highlight", "danger", "success")
        ):
            p.fillRect(10 + i * chip_w, 8, chip_w, 14, QColor(getattr(self._ct, field)))
        p.setPen(QColor(self._ct.text if not self._active else self._ct.highlight))
        f = QFont(T.BODY_FONT)
        f.setPixelSize(T.MIN_FONT_PX)
        p.setFont(f)
        p.drawText(10, 46, self._ct.label)


class _FontCard(QWidget):
    """Clickable preview of one font theme, rendered in its OWN families."""

    chosen = Signal(str)

    def __init__(self, font_theme, parent=None):
        super().__init__(parent)
        self._ft = font_theme
        self._active = False
        self.setFixedHeight(78)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, on: bool) -> None:
        if self._active != on:
            self._active = on
            self.update()

    def mousePressEvent(self, ev) -> None:
        self.chosen.emit(self._ft.id)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.PANEL))
        p.setPen(
            QPen(
                QColor(T.PRIMARY if self._active else T.LINE), 2 if self._active else 1
            )
        )
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        p.setPen(QColor(T.TEXT))
        f = QFont(self._ft.title)
        f.setPixelSize(T.SIZES["title"])
        p.setFont(f)
        p.drawText(12, 28, self._ft.label.upper())
        f = QFont(self._ft.display)
        f.setPixelSize(T.SIZES["display"])
        p.setFont(f)
        p.setPen(QColor(T.MUTED))
        p.drawText(12, 48, f"LABELS  TABS  BUTTONS   ({self._ft.display})")
        f = QFont(self._ft.body)
        f.setPixelSize(T.SIZES["body"])
        p.setFont(f)
        p.setPen(QColor(T.HIGHLIGHT))
        p.drawText(12, 70, "CPU 47%  RAM 12.3 GB  NET UP 41 KB/s")


class ThemesTab(QWidget):
    """PICK A SURFACE: click any color/font card; the change is live. Both
    the panel here and window pieces (charts, tray icon) re-theme at once."""

    def __init__(self, theme_manager, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self._manager = theme_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── colors ───────────────────────────────────────────────────
        colors_panel = PixelPanel("COLOR THEMES")
        self._color_grid = QGridLayout()
        self._color_grid.setSpacing(6)
        self._color_cards: dict[str, _ColorCard] = {}
        for i, ct in enumerate(COLOR_THEMES):
            card = _ColorCard(ct)
            card.chosen.connect(self._choose_color)
            self._color_cards[ct.id] = card
            self._color_grid.addWidget(card, i // 3, i % 3)
        color_host = QWidget()
        color_host.setLayout(self._color_grid)
        colors_panel.body().addWidget(self._wrap_scroll(color_host))
        layout.addWidget(colors_panel, 5)

        # ── fonts ────────────────────────────────────────────────────
        fonts_panel = PixelPanel("FONTS")
        self._font_rows = QVBoxLayout()
        self._font_rows.setSpacing(6)
        self._font_cards: dict[str, _FontCard] = {}
        for ft in FONT_THEMES:
            card = _FontCard(ft)
            card.chosen.connect(self._choose_font)
            self._font_cards[ft.id] = card
            self._font_rows.addWidget(card)
        self._font_rows.addStretch(1)
        font_host = QWidget()
        font_host.setLayout(self._font_rows)
        fonts_panel.body().addWidget(self._wrap_scroll(font_host))
        layout.addWidget(fonts_panel, 5)

        # ── footer ───────────────────────────────────────────────────
        size_lbl = QLabel("UI FONT SIZE")
        size_lbl.setObjectName("panelTitle")
        self._size_box = QComboBox()
        # 14px floor…18px shipped default; everything scales proportionally
        for px in range(14, 19):
            self._size_box.addItem(f"{px}PX" + ("  (DEFAULT)" if px == 18 else ""), px)
        default_index = self._size_box.findData(self._manager.body_px)
        self._size_box.setCurrentIndex(max(0, default_index))
        self._size_box.currentIndexChanged.connect(self._choose_size)
        reset = QPushButton("RESET TO DEFAULT (AMBER / CLASSIC)")
        reset.setObjectName("danger")
        reset.clicked.connect(lambda: self._manager.apply("amber", "classic"))
        footer = QHBoxLayout()
        footer.addWidget(size_lbl)
        footer.addWidget(self._size_box)
        footer.addWidget(reset)
        footer.addStretch(1)
        layout.addLayout(footer)

        self._status = QLabel("CHANGE ANYTHING — IT APPLIES LIVE AND SURVIVES RESTARTS")
        self._status.setObjectName("muted")
        layout.addWidget(self._status)

        self._refresh_active()

    def _wrap_scroll(self, host: QWidget) -> QScrollArea:
        """Fifteen options per section don't fit: same scroll pattern as the
        temperature panel on the dashboard."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        return scroll

    def _refresh_active(self) -> None:
        for tid, card in self._color_cards.items():
            card.set_active(tid == self._manager.color_id)
        for tid, card in self._font_cards.items():
            card.set_active(tid == self._manager.font_id)

    def _choose_color(self, color_id: str) -> None:
        self._manager.apply(color_id, self._manager.font_id)
        self._refresh_active()

    def _choose_font(self, font_id: str) -> None:
        self._manager.apply(self._manager.color_id, font_id)
        self._refresh_active()

    def _choose_size(self, index: int) -> None:
        px = self._size_box.itemData(index)
        if px is not None and int(px) != self._manager.body_px:
            self._manager.set_body_px(int(px))
