from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QPen

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
QSS_PATH = Path(__file__).resolve().parent / "theme.qss"

FONT_FAMILIES = ("Silkscreen", "VT323", "Press Start 2P")

BG = "#0A0A0A"
PANEL = "#141414"
PANEL_ALT = "#0F0F0F"
LINE = "#2E2E2E"
PRIMARY = "#FFD400"
HIGHLIGHT = "#FFE873"
DANGER = "#FF3B30"
SUCCESS = "#9BE800"
TEXT = "#E8E8E8"
MUTED = "#6A6A6A"


def load_fonts(logger=print) -> list[str]:
    loaded: list[str] = []
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            logger(f"[fonts] could not load {path.name}")
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        loaded.extend(families)
    known = {f.split()[0].upper() for f in loaded}
    for family in FONT_FAMILIES:
        if family.upper() not in known and f"{family.split()[0].upper()}" not in known:
            logger(f"[fonts] WARNING: family '{family}' not available — fallback font in use")
    return loaded


def tick_font() -> QFont:
    return QFont("VT323", 10)


def load_theme(app) -> None:
    app.setStyleSheet(QSS_PATH.read_text(encoding="utf-8"))


def pixel_pixmap(size: int = 32, draw=None) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(QColor(BG))
    painter = QPainter(px)
    painter.fillRect(0, 0, size, size, QColor(BG))
    if draw:
        draw(painter, size)
    painter.end()
    return px


def _grid_draw(grid: list[str], palette: dict[str, str]):
    def painter_fn(painter: QPainter, size: int) -> None:
        rows = len(grid)
        cols = max((len(r) for r in grid), default=0)
        if rows == 0 or cols == 0:
            return
        cell = size / max(rows, cols)
        painter.setPen(Qt.PenStyle.NoPen)
        for y, row in enumerate(grid):
            for x, ch in enumerate(row):
                color = palette.get(ch)
                if color is None:
                    continue
                painter.fillRect(int(x * cell), int(y * cell),
                                 max(1, int(cell)), max(1, int(cell)), QColor(color))
    return painter_fn


HEARTBEAT_GRID = [
    "....X...",
    "....X...",
    "...XX...",
    "X..X.XX.",
    "XX.X.X.X",
    ".XXX..XX",
    "..X.....",
    "........",
]

LED_GRID = [
    "XXXXXXXX",
    "X......X",
    "X......X",
    "X......X",
    "X......X",
    "X......X",
    "X......X",
    "XXXXXXXX",
]

APP_ICON_PALETTES = {PRIMARY: "X"}


def app_icon() -> QIcon:
    icon_file = FONTS_DIR.parent / "icons" / "pulse.ico"
    if icon_file.exists():
        return QIcon(str(icon_file))
    px = pixel_pixmap(64, _grid_draw(HEARTBEAT_GRID, {"X": PRIMARY}))
    return QIcon(px)


def status_icon(state: str) -> QIcon:
    color = {"ok": SUCCESS, "warn": PRIMARY, "error": DANGER}.get(state, MUTED)

    def fill(painter: QPainter, size: int) -> None:
        painter.setPen(QPen(QColor(PRIMARY), 2))
        painter.drawRect(1, 1, size - 2, size - 2)
        painter.fillRect(4, 4, size - 8, size - 8, QColor(color))
    px = pixel_pixmap(32, fill)
    return QIcon(px)


def led_pixmap(on: bool, color: str, size: int = 20) -> QPixmap:
    def paint(p: QPainter, s: int) -> None:
        if on:
            p.fillRect(2, 2, s - 4, s - 4, QColor(color))
            p.fillRect(2, 2, (s - 4) // 2, (s - 4) // 4, QColor(HIGHLIGHT))
        else:
            p.fillRect(2, 2, s - 4, s - 4, QColor(PANEL_ALT))
            p.fillRect(2, 2, s - 4, s - 4, QColor(LINE))
    return pixel_pixmap(size, paint)
