from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm import app_settings
from pulse_hwm.alerts.notifier import AlertChannels
from pulse_hwm.app_settings import AppSettings
from pulse_hwm.db import Database
from pulse_hwm.processes import set_low_priority_mode, trim_working_set
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel


class SettingsTab(QWidget):
    # queued: the processes collector lives on its worker thread, so turning
    # settings changes into interval/row updates must cross threads safely
    processes_reconfigure = Signal(float, int)

    def __init__(
        self, db: Database, sites_monitor, alerts, processes_collector=None, parent=None
    ):
        super().__init__(parent)
        self.setObjectName("root")
        self._db = db
        self._monitor = sites_monitor
        self._alerts = alerts
        self._processes = processes_collector
        if processes_collector is not None:
            self.processes_reconfigure.connect(processes_collector.reconfigure)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        panel = PixelPanel("MONITORING")
        form = QFormLayout()
        self.hw_interval = QSpinBox()
        self.hw_interval.setRange(250, 10_000)
        self.hw_interval.setSuffix(" ms")
        self.site_interval = QSpinBox()
        self.site_interval.setRange(5, 3_600)
        self.site_interval.setSuffix(" s")
        self.site_timeout = QDoubleSpinBox()
        self.site_timeout.setRange(1.0, 120.0)
        self.site_timeout.setSuffix(" s")
        self.ssl_warn = QSpinBox()
        self.ssl_warn.setRange(1, 90)
        self.ssl_warn.setSuffix(" d")
        form.addRow("HARDWARE INTERVAL", self.hw_interval)
        form.addRow("WEBSITE INTERVAL", self.site_interval)
        form.addRow("REQUEST TIMEOUT", self.site_timeout)
        form.addRow("SSL WARNING BEFORE", self.ssl_warn)
        panel.body().addLayout(form)
        outer.addWidget(panel)

        alerts_panel = PixelPanel("ALERTS")
        alerts_form = QFormLayout()
        self.sound_box = QCheckBox("8-BIT SOUND")
        self.desktop_box = QCheckBox("DESKTOP TOAST")
        self.webhook_box = QCheckBox("DISCORD / SLACK WEBHOOKS (.env URLs)")
        alerts_form.addRow(self.sound_box)
        alerts_form.addRow(self.desktop_box)
        alerts_form.addRow(self.webhook_box)
        test_row = QHBoxLayout()
        test_btn = QPushButton("TEST ALERT")
        test_btn.setObjectName("success")
        test_btn.clicked.connect(self._test_alert)
        test_row.addWidget(test_btn)
        test_row.addStretch(1)
        body = alerts_panel.body()
        body.addLayout(alerts_form)
        body.addLayout(test_row)
        outer.addWidget(alerts_panel)

        resources_panel = PixelPanel("RESOURCES")
        resources_form = QFormLayout()
        self.proc_interval = QSpinBox()
        self.proc_interval.setRange(1, 60)
        self.proc_interval.setSuffix(" s")
        self.proc_interval.setToolTip(
            "How often the PROCESSES tab rescans (only while visible)"
        )
        self.proc_max_rows = QSpinBox()
        self.proc_max_rows.setRange(50, 2_000)
        self.proc_max_rows.setSuffix(" rows")
        self.proc_max_rows.setToolTip(
            "Cap on rendered process rows, ranked heaviest-first"
        )
        self.limit_resources_box = QCheckBox("LIMIT PULSE RESOURCES")
        self.limit_resources_box.setToolTip(
            "Run below normal CPU priority + trim cold memory every 15 minutes"
        )
        resources_form.addRow("PROCESS SCAN EVERY", self.proc_interval)
        resources_form.addRow("MAX PROCESS ROWS", self.proc_max_rows)
        resources_form.addRow(self.limit_resources_box)
        trim_btn = QPushButton("TRIM MEMORY NOW")
        trim_btn.clicked.connect(self._trim_now)
        resources_form.addRow(trim_btn)
        admin_btn = QPushButton("RESTART AS ADMIN")
        admin_btn.setToolTip(
            "Quit and relaunch elevated — needed for some temperature sensors"
        )
        admin_btn.clicked.connect(self._restart_as_admin)
        resources_form.addRow(admin_btn)
        resources_note = QLabel(
            "PROCESSES tab only scans while visible; caches evict dead processes "
            "every scan so memory stays bounded."
        )
        resources_note.setObjectName("muted")
        resources_note.setWordWrap(True)
        resources_panel.body().addLayout(resources_form)
        resources_panel.body().addWidget(resources_note)
        outer.addWidget(resources_panel)

        data_panel = PixelPanel("DATA")
        data_form = QFormLayout()
        self.retention = QSpinBox()
        self.retention.setRange(1, 365)
        self.retention.setSuffix(" d")
        data_form.addRow("KEEP HISTORY FOR", self.retention)
        prune_btn = QPushButton("APPLY + PRUNE NOW")
        prune_btn.clicked.connect(self._apply)
        data_form.addRow(prune_btn)
        note = QLabel(
            "Webhook URLs are read from .env (gitignored). Values here override .env-only boot defaults."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        data_panel.body().addLayout(data_form)
        data_panel.body().addWidget(note)
        credit = QLabel(
            "Temp sensors via LibreHardwareMonitorLib 0.9.6 (MPL-2.0) — "
            "librehardwaremonitor.org — see THIRD_PARTY.md"
        )
        credit.setObjectName("muted")
        credit.setWordWrap(True)
        data_panel.body().addWidget(credit)
        outer.addWidget(data_panel)

        self.test_banner = QLabel("TEST ALERT DISPATCHED")
        self.test_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.test_banner.setVisible(False)
        self.test_result = QLabel("")
        self.test_result.setObjectName("muted")
        outer.addWidget(self.test_banner)
        outer.addWidget(self.test_result)

        outer.addStretch(1)
        self.load_from(app_settings.load(db))

    def load_from(self, values: AppSettings) -> None:
        self.hw_interval.setValue(values.hardware_interval_ms)
        self.site_interval.setValue(values.website_interval_s)
        self.site_timeout.setValue(values.website_timeout_s)
        self.ssl_warn.setValue(values.ssl_warn_days)
        self.retention.setValue(values.retention_days)
        self.sound_box.setChecked(values.sound_enabled)
        self.desktop_box.setChecked(values.desktop_enabled)
        self.webhook_box.setChecked(values.webhooks_enabled)
        self.proc_interval.setValue(values.process_interval_s)
        self.proc_max_rows.setValue(values.process_max_rows)
        self.limit_resources_box.setChecked(values.limit_resources)

    def collect(self) -> AppSettings:
        return AppSettings(
            hardware_interval_ms=self.hw_interval.value(),
            website_interval_s=self.site_interval.value(),
            website_timeout_s=self.site_timeout.value(),
            ssl_warn_days=self.ssl_warn.value(),
            retention_days=self.retention.value(),
            sound_enabled=self.sound_box.isChecked(),
            desktop_enabled=self.desktop_box.isChecked(),
            webhooks_enabled=self.webhook_box.isChecked(),
            process_interval_s=self.proc_interval.value(),
            process_max_rows=self.proc_max_rows.value(),
            limit_resources=self.limit_resources_box.isChecked(),
        )

    def _apply(self) -> None:
        values = self.collect()
        app_settings.save(self._db, values)
        self._monitor.reconfigure(
            interval_s=values.website_interval_s,
            timeout_s=values.website_timeout_s,
            ssl_warn_days=values.ssl_warn_days,
        )
        self._alerts.set_channels(
            AlertChannels(
                sound=values.sound_enabled,
                desktop=values.desktop_enabled,
                webhooks=values.webhooks_enabled,
            )
        )
        if self._processes is not None:
            self.processes_reconfigure.emit(
                float(values.process_interval_s), int(values.process_max_rows)
            )
        set_low_priority_mode(values.limit_resources)
        self._prune_values(values)

    def _prune_values(self, values: AppSettings) -> None:
        try:
            self._db.prune(values.retention_days)
        except Exception:
            pass

    def _trim_now(self) -> None:
        trim_working_set()

    def _restart_as_admin(self) -> None:
        """Quit clean, then Windows relaunches Pulse with the UAC runas verb.

        The current process must exit AFTER Windows accepts the elevation
        request, otherwise the UAC dialog would pop up over a dead app.
        """
        from PySide6.QtWidgets import QApplication

        from pulse_hwm.util import restart_command, shell_runas

        exe, args = restart_command()
        if shell_runas(exe, args):
            self.test_result.setText("relaunching as ADMIN…")
            QApplication.quit()
        else:
            self.test_result.setText("restart cancelled (UAC denied)")

    def _test_alert(self) -> None:
        sent = self._alerts.notify(
            "info",
            "PULSE-HWM TEST",
            "This is what an alert looks like.",
            play_sound=True,
        )
        parts = []
        if sent.get("sound_requested"):
            parts.append("SOUND")
        if sent.get("desktop"):
            parts.append("TOAST")
        hooks = sent.get("webhooks") or 0
        if hooks:
            parts.append(f"{hooks} WEBHOOK(S)")
        self.test_result.setText(
            "fired: "
            + (", ".join(parts) if parts else "no channels enabled (toggles above)")
        )
        self.test_banner.setVisible(True)
        self.test_banner.setStyleSheet(
            "background-color: #FFD400; color: #0A0A0A;"
            "font-family: 'Silkscreen'; font-size: 14px; padding: 10px;"
        )
        QTimer.singleShot(2500, lambda: self.test_banner.setVisible(False))
