from __future__ import annotations

from collections import deque

import pyqtgraph as pg
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from pulse_hwm.ui import theme as T

pg.setConfigOptions(antialias=False, background="#0A0A0A", foreground=T.LINE)


def _time_axis() -> pg.DateAxisItem:
    axis = pg.DateAxisItem(orientation="bottom")
    axis.setPen(pg.mkPen(T.LINE, width=1))
    axis.setTextPen(pg.mkPen(QColor(T.MUTED)))
    axis.setStyle(tickFont=T.tick_font())
    return axis


class PixelPlot(QWidget):
    """Rolling line chart with pixel styling (no smoothing, hard amber trace)."""

    MAX_POINTS = 180

    def __init__(self, pen_color: str = T.PRIMARY, fill: bool = True, parent=None):
        super().__init__(parent)
        self._data: deque[tuple[float, float]] = deque(maxlen=self.MAX_POINTS)
        self._plot = pg.PlotWidget(axisItems={"bottom": _time_axis()})

        self._plot.setBackground("#0A0A0A")
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.hideButtons()
        self._plot.getPlotItem().setContentsMargins(0, 0, 0, 0)

        pen = pg.mkPen(QColor(pen_color), width=1, style=pg.QtCore.Qt.PenStyle.SolidLine)
        self._curve = self._plot.plot([], [], pen=pen, fillLevel=0.0 if fill else None)
        if fill:
            brush_color = QColor(pen_color)
            brush_color.setAlpha(40)
            self._curve.setFillBrush(pg.mkBrush(brush_color))

        ax = self._plot.getAxis("left")
        ax.setPen(pg.mkPen(T.LINE, width=1))
        ax.setTextPen(pg.mkPen(QColor(T.MUTED)))
        ax.setStyle(tickFont=T.tick_font())

        layout = pg.QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot)

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
        self._data.extend(series[-self.MAX_POINTS:])
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
