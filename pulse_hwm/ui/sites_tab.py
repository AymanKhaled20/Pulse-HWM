from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from pulse_hwm.collectors.websites import WebsiteMonitor, uptime_percent
from pulse_hwm.db import Database
from pulse_hwm.ui import theme as T
from pulse_hwm.ui.widgets.charts import PixelPlot
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel

COLUMNS = 8
COL_LED, COL_NAME, COL_URL, COL_STATUS, COL_LATENCY, COL_UPTIME, COL_SSL, COL_LAST = range(COLUMNS)

REFRESH_UPTIME_EVERY = 5
SPARK_POINTS = 120


class _LedWidget(QWidget):
    def __init__(self, state: str = "pending", parent=None):
        super().__init__(parent)
        self._state = state
        self.setFixedSize(30, 20)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        color = {"ok": T.SUCCESS, "down": T.DANGER, "pending": T.LINE}.get(self._state, T.LINE)
        p.fillRect(3, 3, self.width() - 6, self.height() - 6, QColor(color))
        p.setPen(QPen(QColor(T.LINE), 1))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        p.end()


class SiteDialog(QDialog):
    def __init__(self, parent=None, site: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("EDIT SITE" if site else "ADD SITE")
        self.setMinimumWidth(420)
        form = QFormLayout(self)

        self.name_edit = QLineEdit(site.get("name", "") if site else "")
        self.url_edit = QLineEdit(site.get("url", "") if site else "")
        self.url_edit.setPlaceholderText("https://example.com  (name optional, filled from URL)")
        self.method_box = QComboBox()
        self.method_box.addItems(["GET", "HEAD"])
        self.method_box.setCurrentText((site.get("method") or "GET").upper() if site else "GET")
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 120.0)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(float(site.get("timeout_s") or 10.0))
        self.expected_spin = QDoubleSpinBox()
        self.expected_spin.setDecimals(0)
        self.expected_spin.setRange(100, 599)
        self.expected_spin.setValue(float(site.get("expected_status") or 200))
        self.keyword_edit = QLineEdit(site.get("keyword", "") if site else "")
        self.keyword_edit.setPlaceholderText("optional — text that must appear in body")

        form.addRow("NAME", self.name_edit)
        form.addRow("URL", self.url_edit)
        form.addRow("METHOD", self.method_box)
        form.addRow("TIMEOUT", self.timeout_spin)
        form.addRow("EXPECTED STATUS", self.expected_spin)
        form.addRow("KEYWORD", self.keyword_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "url": self.url_edit.text().strip(),
            "method": self.method_box.currentText(),
            "timeout_s": self.timeout_spin.value(),
            "expected_status": int(self.expected_spin.value()),
            "keyword": self.keyword_edit.text().strip(),
        }


class SitesTab(QWidget):
    def __init__(self, db: Database, monitor: WebsiteMonitor, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self._db = db
        self._monitor = monitor
        self._rows: dict[int, int] = {}
        self._leds: dict[int, _LedWidget] = {}
        self._cycle = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        top = PixelPanel("SITES")
        self.table = QTableWidget(0, COLUMNS)
        self.table.setHorizontalHeaderLabels(
            ["", "NAME", "URL", "STATUS", "LATENCY", "UPTIME 24H", "SSL", "LAST CHECK"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_URL, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_LED, 34)
        self.table.setColumnWidth(COL_NAME, 140)
        self.table.setColumnWidth(COL_STATUS, 80)
        self.table.setColumnWidth(COL_LATENCY, 90)
        self.table.setColumnWidth(COL_UPTIME, 110)
        self.table.setColumnWidth(COL_SSL, 90)
        self.table.setColumnWidth(COL_LAST, 110)
        top.body().addWidget(self.table)

        buttons = QHBoxLayout()
        add_btn = QPushButton("ADD")
        add_btn.clicked.connect(self._add_site)
        edit_btn = QPushButton("EDIT")
        edit_btn.clicked.connect(self._edit_site)
        remove_btn = QPushButton("REMOVE")
        remove_btn.setObjectName("danger")
        remove_btn.clicked.connect(self._remove_site)
        check_btn = QPushButton("CHECK NOW")
        check_btn.setObjectName("success")
        check_btn.clicked.connect(self._check_now)
        for b in (add_btn, edit_btn, remove_btn, check_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        top.body().addLayout(buttons)
        layout.addWidget(top, 2)

        chart_panel = PixelPanel("LATENCY — SELECTED SITE (24H)")
        self.latency_chart = PixelPlot(fill=False)
        chart_panel.body().addWidget(self.latency_chart, 1)
        layout.addWidget(chart_panel, 1)

        self.table.cellClicked.connect(self._on_row_clicked)
        self._monitor.checked.connect(self._on_checked)
        self._monitor.ssl_updated.connect(self._on_ssl)
        self.reload_sites()

    # ── table management ──────────────────────────────────
    def reload_sites(self) -> None:
        rows = [dict(r) for r in self._db.get_sites(include_disabled=True)]
        wanted = {int(r["id"]) for r in rows}
        for site_id in list(self._rows):
            if site_id not in wanted:
                row = self._rows.pop(site_id)
                self._leds.pop(site_id, None)
                self.table.removeRow(row)
                self._reindex()
        for site in rows:
            self._ensure_row(site)
        self._refresh_uptime()

    def _ensure_row(self, site: dict) -> None:
        site_id = int(site["id"])
        if site_id in self._rows:
            row = self._rows[site_id]
            self.table.item(row, COL_NAME).setText(site["name"])
            self.table.item(row, COL_URL).setText(site["url"])
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[site_id] = row

        led = _LedWidget()
        self.table.setCellWidget(row, COL_LED, led)
        self._leds[site_id] = led

        self.table.setItem(row, COL_NAME, QTableWidgetItem(site["name"]))
        url_item = QTableWidgetItem(site["url"])
        url_item.setForeground(QBrush(QColor(T.MUTED)))
        self.table.setItem(row, COL_URL, url_item)
        self.table.setItem(row, COL_STATUS, QTableWidgetItem("PENDING"))
        self.table.setItem(row, COL_LATENCY, QTableWidgetItem("—"))
        self.table.setItem(row, COL_UPTIME, QTableWidgetItem("—"))
        self.table.setItem(row, COL_SSL, QTableWidgetItem("—"))
        self.table.setItem(row, COL_LAST, QTableWidgetItem("—"))

    def _reindex(self) -> None:
        ordered = sorted(self._rows.items(), key=lambda kv: kv[1])
        for i, (site_id, _row) in enumerate(ordered):
            if self._rows[site_id] != i:
                self._rows[site_id] = i

    # ── actions ────────────────────────────────────────────
    def _selected_site_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        for site_id, table_row in self._rows.items():
            if table_row == row:
                return site_id
        return None

    def _on_row_clicked(self, row: int, _col: int) -> None:
        site_id = self._site_id_of_row(row)
        if site_id is not None:
            self._load_latency_chart(site_id)

    def _site_id_of_row(self, row: int) -> int | None:
        for site_id, table_row in self._rows.items():
            if table_row == row:
                return site_id
        return None

    def _add_site(self) -> None:
        dlg = SiteDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            values = dlg.values()
            if not values["url"]:
                return
            if not values["url"].startswith(("http://", "https://")):
                values["url"] = "https://" + values["url"]
            if not values["name"]:
                from urllib.parse import urlsplit
                values["name"] = (urlsplit(values["url"]).hostname or values["url"]).removeprefix("www.")
            self._db.add_site(**values)
            self.reload_sites()
            self._monitor.run_cycle_now()

    def _edit_site(self) -> None:
        site_id = self._selected_site_id()
        if site_id is None:
            return
        site = None
        for r in self._db.get_sites():
            if int(r["id"]) == site_id:
                site = dict(r)
                break
        if not site:
            return
        dlg = SiteDialog(self, site=site)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        values = dlg.values()
        if not values["name"] or not values["url"]:
            return
        with self._db._lock:
            self._db._conn.execute(
                "UPDATE sites SET name=?, url=?, method=?, timeout_s=?,"
                " expected_status=?, keyword=? WHERE id=?",
                (values["name"], values["url"], values["method"], values["timeout_s"],
                 values["expected_status"], values["keyword"], site_id),
            )
            self._db._conn.commit()
        self.reload_sites()
        self._monitor.run_cycle_now()

    def _remove_site(self) -> None:
        site_id = self._selected_site_id()
        if site_id is None:
            return
        self._db.remove_site(site_id)
        self.reload_sites()

    def _check_now(self) -> None:
        self._monitor.run_cycle_now()

    # ── updates ────────────────────────────────────────────
    def _on_checked(self, result: dict) -> None:
        site_id = int(result["site_id"])
        row = self._rows.get(site_id)
        if row is None:
            self.reload_sites()
            row = self._rows.get(site_id)
        if row is None:
            return
        led = self._leds.get(site_id)
        if led:
            led.set_state("ok" if result["ok"] else "down")
        self.table.item(row, COL_STATUS).setText(
            str(result["status_code"]) if result["status_code"] else "ERR"
        )
        self.table.item(row, COL_LATENCY).setText(f"{result['latency_ms']:.0f} ms")
        self.table.item(row, COL_LAST).setText(
            time.strftime("%H:%M:%S", time.localtime(result["ts"]))
        )
        self._tint(self.table.item(row, COL_STATUS), T.SUCCESS if result["ok"] else T.DANGER)
        selected = self._selected_site_id()
        if selected == site_id:
            self._load_latency_chart(site_id)
        self._cycle += 1
        if self._cycle % REFRESH_UPTIME_EVERY == 0:
            self._refresh_uptime()

    def _on_ssl(self, site_id: int, days: int) -> None:
        row = self._rows.get(site_id)
        if row is None:
            return
        item = self.table.item(row, COL_SSL)
        if days <= 0:
            item.setText("EXPIRED!")
            self._tint(item, T.DANGER)
        elif days <= self._monitor._ssl_warn_days:
            item.setText(f"{days}d !")
            self._tint(item, T.DANGER)
        else:
            item.setText(f"{days}d")
            self._tint(item, T.MUTED)

    def _load_latency_chart(self, site_id: int) -> None:
        rows = self._db.checks_since(site_id, time.time() - 86400)
        series = [(r["ts"], r["latency_ms"]) for r in rows[-SPARK_POINTS:]]
        self.latency_chart.set_series(series)

    def _refresh_uptime(self) -> None:
        for site in self._db.get_sites():
            site_id = int(site["id"])
            row = self._rows.get(site_id)
            if row is None:
                continue
            pct = uptime_percent(self._db, site_id, 86400)
            self.table.item(row, COL_UPTIME).setText(f"{pct:.1f}%" if pct is not None else "—")

    def _tint(self, item: QTableWidgetItem, color: str) -> None:
        item.setForeground(QBrush(QColor(color)))
