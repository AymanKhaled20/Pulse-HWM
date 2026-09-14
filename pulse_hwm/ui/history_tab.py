from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm.db import Database
from pulse_hwm.ui import theme as T
from pulse_hwm.ui.widgets.charts import PixelPlot
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel

LEVEL_COLOR = {
    "ERROR": T.DANGER,
    "WARN": T.PRIMARY,
    "INFO": T.SUCCESS,
    "DEBUG": T.MUTED,
}


class HistoryTab(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self._db = db

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.s(10), T.s(10), T.s(10), T.s(10))
        layout.setSpacing(T.s(10))

        charts_row = QHBoxLayout()
        self.cpu_plot = PixelPlot()
        self.mem_plot = PixelPlot(accent="highlight")
        cpu_cap = self._cap("CPU % (1H)", self.cpu_plot)
        mem_cap = self._cap("RAM % (1H)", self.mem_plot)
        charts_row.addWidget(cpu_cap, 1)
        charts_row.addWidget(mem_cap, 1)
        layout.addLayout(charts_row, 2)

        events_panel = PixelPanel("EVENT LOG")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["TIME", "LEVEL", "EVENT"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 70)
        events_panel.body().addWidget(self.table)

        refresh = QPushButton("REFRESH")
        refresh.clicked.connect(self.refresh)
        events_panel.body().addWidget(refresh)
        layout.addWidget(events_panel, 2)

        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(10_000)
        self.refresh()

    def _cap(self, title: str, child: QWidget) -> QWidget:
        cap = QWidget()
        col = QVBoxLayout(cap)
        col.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(title)
        lbl.setObjectName("muted")
        col.addWidget(lbl)
        col.addWidget(child, 1)
        return cap

    def refresh(self) -> None:
        now = time.time()
        for plot, metric in ((self.cpu_plot, "cpu_total"), (self.mem_plot, "mem_pct")):
            rows = self._db.hardware_series(metric, now - 3600)
            plot.set_series([(r["ts"], r["value"]) for r in rows])

        events = self._db.events_since(now - 7 * 86400, limit=300)
        self.table.setRowCount(len(events))
        for r, ev in enumerate(events):
            time_item = QTableWidgetItem(
                time.strftime("%m-%d %H:%M:%S", time.localtime(ev["ts"]))
            )
            level_item = QTableWidgetItem(ev["level"])
            message_item = QTableWidgetItem(ev["message"])
            level_item.setForeground(
                QBrush(QColor(LEVEL_COLOR.get(ev["level"], T.TEXT)))
            )
            for c, item in enumerate((time_item, level_item, message_item)):
                if c < 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(r, c, item)
