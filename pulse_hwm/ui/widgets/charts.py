from __future__ import annotations

from collections import deque

import pyqtgraph as pg
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from pulse_hwm.ui import theme as T


def _time_axis() -> pg.DateAxisItem:
    axis = pg.DateAxisItem(orientation="bottom")
    _style_axis(axis)
    return axis


def _style_axis(axis) -> None:
    """(Re)style one pyqtgraph axis from the ACTIVE palette + body font.

    Pens and tick fonts are cached by pyqtgraph, so theme switching must
    call this again — that's what PixelPlot.apply_theme() does.
    """
    axis.setPen(pg.mkPen(QColor(T.LINE), width=1))
    axis.setTextPen(pg.mkPen(QColor(T.MUTED)))
    axis.setStyle(tickFont=T.tick_font())


class PixelPlot(QWidget):
    """Rolling line chart with pixel styling (no smoothing, hard accent trace)."""

    MAX_POINTS = 180

    def __init__(self, accent: str = "primary", fill: bool = True, parent=None):
        """`accent` is a palette role name ("primary" / "highlight") — NOT a
        hex string. A hex default would be frozen at import time and never
        follow theme switches."""
        super().__init__(parent)
        self._accent = accent
        self._fill = fill
        self._data: deque[tuple[float, float]] = deque(maxlen=self.MAX_POINTS)
        self._plot = pg.PlotWidget(axisItems={"bottom": _time_axis()})

        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.hideButtons()
        self._plot.getPlotItem().setContentsMargins(0, 0, 0, 0)

        self._curve = self._plot.plot([], [], fillLevel=0.0 if fill else None)

        _style_axis(self._plot.getAxis("left"))

        layout = pg.QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot)
        # keep the plot area as tall as the original HISTORY layout even
        # when the processes table below grows its row height
        self.setMinimumHeight(180)
        self.apply_theme()

    def apply_theme(self) -> None:
        """Re-apply the active palette to cached pens/brushes/backgrounds."""
        accent = T.PRIMARY if self._accent == "primary" else T.HIGHLIGHT
        pen = pg.mkPen(QColor(accent), width=1, style=pg.QtCore.Qt.PenStyle.SolidLine)
        self._curve.setPen(pen)
        if self._fill:
            brush_color = QColor(accent)
            brush_color.setAlpha(40)
            self._curve.setFillBrush(pg.mkBrush(brush_color))
        self._plot.setBackground(QColor(T.BG))
        for name in ("bottom", "left"):
            _style_axis(self._plot.getAxis(name))
        self._plot.update()

    def add_sample(self, ts: float, value: float) -> None:
        self._data.append((ts, value))
        if not self._data:
            return
        xs = [x for x, _ in self._data]
        ys = [y for _, y in self._data]
        self._curve.setData(xs, ys)
        self._auto_range_y()

    def set_series(self, series: list[tuple[float, float]]) -> None:
        self._data.clear()
        self._data.extend(series[-self.MAX_POINTS :])
        if series:
            xs = [x for x, _ in self._data]
            ys = [y for _, y in self._data]
            self._curve.setData(xs, ys)
            self._auto_range_y()

    def _auto_range_y(self) -> None:
        if not self._data:
            return
        ymax = max(y for _, y in self._data)
        ceil = max(10.0, ymax * 1.25)
        self._plot.setYRange(0, ceil, padding=0.02)
        self._plot.enableAutoRange(x=True, y=False)

    def clear(self) -> None:
        self._data.clear()
        self._curve.setData([], [])
