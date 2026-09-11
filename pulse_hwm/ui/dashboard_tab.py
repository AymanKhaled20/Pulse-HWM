from __future__ import annotations

import platform
import socket

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm.collectors.hardware import HardwareCollector
from pulse_hwm.ui import theme as T
from pulse_hwm.ui.widgets.charts import PixelPlot
from pulse_hwm.ui.widgets.gauges import CpuCoreGrid, GaugeBar, StatRow
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel
from pulse_hwm.util import human_bytes, human_rate, human_uptime, short_cpu_name


class Caption(QWidget):
    def __init__(self, title: str, child: QWidget, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(title)
        lbl.setObjectName("muted")
        col.addWidget(lbl)
        col.addWidget(child, 1)


class DashboardTab(QWidget):
    def __init__(self, collector: HardwareCollector, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self._collector = collector

        self._disk_rows: dict[str, tuple[GaugeBar, QLabel]] = {}
        # temperature row widgets keyed by sensor name (rebuilt only when the
        # sensor set changes; otherwise updated in place — see _refresh_temps)
        self._temp_rows: dict[str, StatRow] = {}
        self._temp_names: tuple[str, ...] = ()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        grid = QGridLayout()
        grid.setSpacing(10)
        outer.addLayout(grid)

        # row 0 — system / gpu / temps (temps panel spans down the right rail)
        grid.addWidget(self._make_system_panel(), 0, 0)
        grid.addWidget(self._make_gpu_panel(), 0, 1)
        grid.addWidget(self._make_temp_panel(), 0, 2, 3, 1)

        # row 1 — cpu / memory
        grid.addWidget(self._make_cpu_panel(), 1, 0)
        grid.addWidget(self._make_mem_panel(), 1, 1)

        # row 2 — disks / network
        grid.addWidget(self._make_disk_panel(), 2, 0)
        grid.addWidget(self._make_net_panel(), 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setRowStretch(0, 0)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(2, 1)

        # row 3 — history charts
        history = PixelPanel("HISTORY")
        self.cpu_chart = PixelPlot()
        self.net_chart = PixelPlot(accent="highlight")
        row = QHBoxLayout()
        row.addWidget(Caption("CPU %", self.cpu_chart), 1)
        row.addWidget(Caption("NET DOWN", self.net_chart), 1)
        history.body().addLayout(row)

        tracker = PixelPanel("TOP PROCESSES")
        tracker.body().addWidget(self._make_proc_table())

        # HISTORY takes the majority so the plots stay as tall as the
        # original layout (see the reference screenshot); TOP PROCESSES
        # still gets a readable chunk below
        outer.addWidget(history, 2)
        outer.addWidget(tracker, 1)

        collector.updated.connect(self._on_sample)

    # ── panel factories ────────────────────────────────────
    def _make_system_panel(self) -> PixelPanel:
        panel = PixelPanel("SYSTEM")
        body = panel.body()
        host = QLabel(socket.gethostname())
        host.setObjectName("stat")
        self.os_label = QLabel(
            f"{platform.system()} {platform.release()} · {platform.machine()}"
        )
        self.os_label.setObjectName("muted")
        self.cpu_model = QLabel("")
        self.cpu_model.setObjectName("muted")
        self.cpu_model.setWordWrap(True)
        self.uptime_row = StatRow("UPTIME")
        body.addWidget(host)
        body.addWidget(self.os_label)
        body.addWidget(self.cpu_model)
        body.addWidget(self.uptime_row)
        self.batt_gauge = GaugeBar()
        self.batt_label = QLabel("N/A")
        self.batt_label.setObjectName("muted")
        body.addLayout(self._gauge_row("BATT", self.batt_gauge, self.batt_label))
        return panel

    def _make_gpu_panel(self) -> PixelPanel:
        panel = PixelPanel("GPU")
        self.gpu_name = QLabel("detecting…")
        self.gpu_name.setObjectName("muted")
        self.gpu_util = GaugeBar()
        self.gpu_util_label = QLabel("0%")
        self.gpu_util_row = self._gauge_row("LOAD", self.gpu_util, self.gpu_util_label)
        self.gpu_vram = StatRow("VRAM")
        self.gpu_temp = StatRow("GPU TEMP")
        body = panel.body()
        body.addWidget(self.gpu_name)
        body.addLayout(self.gpu_util_row)
        body.addWidget(self.gpu_vram)
        body.addWidget(self.gpu_temp)
        return panel

    def _make_temp_panel(self) -> PixelPanel:
        panel = PixelPanel("TEMPERATURES")
        self.temp_na = QLabel(
            "N/A — run  py scripts/lhm.py fetch  once for hardware temps"
        )
        self.temp_na.setObjectName("muted")
        self.temp_na.setWordWrap(True)
        self.temp_rows_container = QVBoxLayout()
        inner = QWidget()
        inner.setLayout(self.temp_rows_container)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        body = panel.body()
        body.addWidget(scroll, 1)
        body.addWidget(self.temp_na)
        return panel

    def _make_cpu_panel(self) -> PixelPanel:
        panel = PixelPanel("CPU")
        body = panel.body()
        top_row = QHBoxLayout()
        self.cpu_gauge = GaugeBar()
        self.cpu_pct = QLabel("0%")
        self.cpu_pct.setObjectName("stat")
        top_row.addWidget(self.cpu_gauge, 1)
        top_row.addWidget(self.cpu_pct)
        body.addLayout(top_row)
        row = QHBoxLayout()
        left = QVBoxLayout()
        self.cpu_freq_stat = QLabel("N/A")
        freq_row = self._bare_row(QLabel("FREQ"), self.cpu_freq_stat)
        left.addLayout(freq_row)
        self.led = GaugeLed()
        led_row = QHBoxLayout()
        led_row.addWidget(self.led)
        led_row.addWidget(QLabel("SAMPLING"))
        left.addLayout(led_row)
        left.addStretch(1)
        row.addLayout(left, 1)
        self.core_grid = CpuCoreGrid()
        self.core_grid.setMinimumWidth(240)
        row.addWidget(self.core_grid, 2)
        body.addLayout(row)
        return panel

    def _make_mem_panel(self) -> PixelPanel:
        panel = PixelPanel("MEMORY")
        body = panel.body()
        self.mem_gauge = GaugeBar()
        self.mem_pct = QLabel("0%")
        self.mem_pct.setObjectName("stat")
        row = self._gauge_row("RAM", self.mem_gauge, self.mem_pct)
        body.addLayout(row)
        self.swap_gauge = GaugeBar()
        self.swap_pct = QLabel("0%")
        body.addLayout(self._gauge_row("SWAP", self.swap_gauge, self.swap_pct))
        self.mem_used_stat = QLabel("")
        body.addLayout(self._bare_row(QLabel("RAM USED"), self.mem_used_stat))
        self.mem_free_stat = QLabel("")
        body.addLayout(self._bare_row(QLabel("RAM FREE"), self.mem_free_stat))
        self.swap_used_stat = QLabel("")
        body.addLayout(self._bare_row(QLabel("SWAP USED"), self.swap_used_stat))
        return panel

    def _make_disk_panel(self) -> PixelPanel:
        panel = PixelPanel("DISKS")
        self.disk_rows_container = QVBoxLayout()
        self.disk_rows_container.setSpacing(4)
        self.disk_io_row = QHBoxLayout()
        self.disk_read = StatRow("READ")
        self.disk_write = StatRow("WRITE")
        self.disk_io_row.addWidget(self.disk_read, 1)
        self.disk_io_row.addWidget(self.disk_write, 1)
        body = panel.body()
        body.addLayout(self.disk_rows_container)
        body.addLayout(self.disk_io_row)
        return panel

    def _make_net_panel(self) -> PixelPanel:
        panel = PixelPanel("NETWORK")
        body = panel.body()
        self.net_down_gauge = GaugeBar()
        self.net_down_label = QLabel("0 B/s")
        body.addLayout(
            self._gauge_row("DOWN", self.net_down_gauge, self.net_down_label)
        )
        self.net_up_gauge = GaugeBar()
        self.net_up_label = QLabel("0 B/s")
        body.addLayout(self._gauge_row("UP", self.net_up_gauge, self.net_up_label))
        self.net_total = StatRow("TOTALS RX / TX")
        body.addWidget(self.net_total)
        return panel

    def _make_proc_table(self) -> QTableWidget:
        self.proc_table = QTableWidget(0, 4)
        self.proc_table.setHorizontalHeaderLabels(["PROCESS", "CPU %", "MEM %", "PID"])
        self.proc_table.verticalHeader().setVisible(False)
        self.proc_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.proc_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.proc_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.proc_table.setShowGrid(False)
        self.proc_table.setFixedHeight(190)
        return self.proc_table

    # ── layout helpers ─────────────────────────────────────
    def _gauge_row(self, label: str, gauge: GaugeBar, value: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setObjectName("muted")
        lbl.setFixedWidth(46)
        row.addWidget(lbl)
        row.addWidget(gauge, 1)
        row.addWidget(value)
        return row

    def _bare_row(self, left: QLabel, right: QWidget) -> QHBoxLayout:
        left.setObjectName("muted")
        row = QHBoxLayout()
        row.addWidget(left)
        row.addStretch(1)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(right)
        return row

    # ── per-sample update ──────────────────────────────────
    def _on_sample(self, snap: dict) -> None:
        cpu = snap["cpu"]
        if not self.cpu_model.text():
            self.cpu_model.setText(short_cpu_name(cpu["name"]))
        self.cpu_gauge.set_value(cpu["total"] / 100)
        self.cpu_pct.setText(f"{cpu['total']:.0f}%")
        self.core_grid.set_values(cpu["cores"])
        freq = cpu.get("freq_mhz")
        self.cpu_freq_stat.setText(f"{freq / 1000:.2f} GHz" if freq else "N/A")
        self.uptime_row.set_value(human_uptime(cpu["uptime_s"]))
        self.cpu_chart.add_sample(snap["ts"], cpu["total"])
        self.led.set_state(True, T.SUCCESS if cpu["total"] < 80 else T.DANGER)

        mem = snap["mem"]
        self.mem_gauge.set_value(mem["pct"] / 100)
        self.mem_pct.setText(f"{mem['pct']:.0f}%")
        self.swap_gauge.set_value(mem["swap_pct"] / 100)
        self.swap_pct.setText(f"{mem['swap_pct']:.0f}%")
        self.mem_used_stat.setText(human_bytes(mem["used"]))
        self.mem_free_stat.setText(human_bytes(mem["total"] - mem["used"]))
        self.swap_used_stat.setText(human_bytes(mem["swap_used"]))

        self._refresh_disks(snap["disks"])
        diskio = snap.get("diskio") or {}
        self.disk_read.set_value(human_rate(diskio.get("read_bps")))
        self.disk_write.set_value(human_rate(diskio.get("write_bps")))

        net = snap["net"]
        down, up = net.get("rx_bps", 0.0), net.get("tx_bps", 0.0)
        self.net_down_label.setText(human_rate(down))
        self.net_up_label.setText(human_rate(up))
        self.net_down_gauge.set_value(self._net_frac(down))
        self.net_up_gauge.set_value(self._net_frac(up))
        self.net_total.set_value(
            f"{human_bytes(net.get('rx_total'))} / {human_bytes(net.get('tx_total'))}"
        )
        self.net_chart.add_sample(snap["ts"], down)

        self._refresh_gpu(snap.get("gpu"))
        self._refresh_temps(snap.get("temps"))
        self._refresh_battery(snap.get("battery"))
        self._refresh_procs(snap.get("procs"))

    def _net_frac(self, bps: float) -> float:
        return max(0.0, min(1.0, (bps or 0.0) / 125e6))

    # ── dynamic sections ───────────────────────────────────
    def _refresh_disks(self, disks: list[dict]) -> None:
        seen = {d["mount"] for d in disks}
        for mount in list(self._disk_rows):
            if mount not in seen:
                _gauge, _label, wrapper = self._disk_rows.pop(mount)
                self.disk_rows_container.removeWidget(wrapper)
                wrapper.deleteLater()
        for d in disks:
            entry = self._disk_rows.get(d["mount"])
            if entry is None:
                gauge = GaugeBar()
                label = QLabel("")
                label.setObjectName("muted")
                row = QHBoxLayout()
                mount_lbl = QLabel(d["mount"])
                mount_lbl.setObjectName("muted")
                mount_lbl.setFixedWidth(48)
                row.addWidget(mount_lbl)
                row.addWidget(gauge, 1)
                row.addWidget(label)
                wrapper = QWidget()
                wrapper.setLayout(row)
                self.disk_rows_container.addWidget(wrapper)
                self._disk_rows[d["mount"]] = (gauge, label, wrapper)
                entry = self._disk_rows[d["mount"]]
            gauge, label, _w = entry
            gauge.set_value(d["pct"] / 100)
            label.setText(f"{human_bytes(d['used'])} / {human_bytes(d['total'])}")

    def _refresh_gpu(self, gpu: dict | None) -> None:
        devices = (gpu or {}).get("devices") or []
        if not devices:
            self.gpu_util.set_value(0)
            self.gpu_util_label.setText("N/A")
            self.gpu_vram.set_value("N/A")
            self.gpu_temp.set_value("N/A")
            self.gpu_name.setText("N/A — no NVIDIA GPU detected")
            return
        d = devices[0]
        self.gpu_name.setText(str(d["name"]))
        self.gpu_util.set_value(d["util_pct"] / 100)
        self.gpu_util_label.setText(f"{d['util_pct']:.0f}%")
        self.gpu_vram.set_value(
            f"{human_bytes(d['vram_used'])} / {human_bytes(d['vram_total'])}"
        )
        temp = d.get("temp_c")
        self.gpu_temp.set_value(f"{temp:.0f} °C" if temp is not None else "N/A")

    @staticmethod
    def _categorize_temps(temps: list[dict]) -> list[dict]:
        """Curate + rename + order: CPU, GPU, SSD, then board/misc."""
        rows = []
        for t in temps:
            label = str(t["label"])
            if " — " in label:
                _hw, sensor = label.split(" — ", 1)
            else:
                _hw, sensor = label, label
            sensor = sensor.strip()
            if "Distance to TjMax" in sensor or sensor in (
                "Warning Temperature",
                "Critical Temperature",
                "Core Max",
            ):
                continue
            if "Core Average" in sensor:
                continue
            if sensor == "CPU Package":
                rows.append({**t, "name": "CPU", "prio": 0})
            elif sensor == "Composite Temperature":
                rows.append({**t, "name": "SSD", "prio": 2})
            elif sensor.startswith("GPU"):
                rows.append({**t, "name": sensor, "prio": 1})
            elif (
                "Nuvoton" in _hw
                or "Super I/O" in _hw
                or "Winbond" in _hw
                or "ITE" in _hw
            ):
                rows.append({**t, "name": f"M/B {sensor}", "prio": 3})
            else:
                rows.append({**t, "name": sensor[:30], "prio": 8})
        rows.sort(key=lambda r: (r["prio"], r["name"]))
        return rows

    def _refresh_temps(self, temps: list[dict] | None) -> None:
        # fast path: same sensor set as last time -> touch only value labels.
        # Rebuilding the row widgets every 5s created constant widget churn.
        if temps and len(self._temp_rows) == len(temps):
            categorized = self._categorize_temps(temps)
            if tuple(str(t["name"])[:38] for t in categorized) == self._temp_names:
                for t in categorized:
                    name = str(t["name"])[:38]
                    value = t.get("temp")
                    self._temp_rows[name].set_value(
                        f"{value:.0f} °C" if value is not None else "N/A"
                    )
                return
        while self.temp_rows_container.count():
            item = self.temp_rows_container.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.temp_na.setVisible(not temps)
        self._temp_rows.clear()
        if not temps:
            self._temp_names = ()
            return
        for t in self._categorize_temps(temps):
            name = str(t["name"])[:38]
            value = t.get("temp")
            row = StatRow(name)
            row.set_value(f"{value:.0f} °C" if value is not None else "N/A")
            self._temp_rows[name] = row
            self.temp_rows_container.addWidget(row)
        # remember the set so later refreshes only update values in place
        self._temp_names = tuple(self._temp_rows)

    def _refresh_battery(self, battery: dict | None) -> None:
        if not battery:
            self.batt_gauge.set_value(0)
            self.batt_label.setText("N/A")
            return
        self.batt_gauge.set_value(battery["pct"] / 100)
        plugged = " ▸charging" if battery["plugged"] else ""
        self.batt_label.setText(f"{battery['pct']:.0f}%{plugged}")

    def _refresh_procs(self, procs: list[dict]) -> None:
        if not procs:
            return
        self.proc_table.setRowCount(len(procs))
        for r, proc in enumerate(procs):
            cells = (
                proc["name"][:40],
                f"{proc['cpu']:.1f}",
                f"{proc['mem']:.1f}",
                str(proc["pid"]),
            )
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.proc_table.setItem(r, c, item)


class GaugeLed(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setProperty("ok", True)

    def set_state(self, on: bool, color=None) -> None:
        self.setProperty("ok", on)
        self.update()

    def paintEvent(self, ev) -> None:
        from PySide6.QtGui import QColor, QPainter, QPen

        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.PANEL))
        color = T.SUCCESS if self.property("ok") else T.DANGER
        p.fillRect(2, 2, self.width() - 4, self.height() - 4, QColor(color))
        p.setPen(QPen(QColor(T.LINE), 1))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        p.end()
