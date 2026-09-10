from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from pulse_hwm import config
from pulse_hwm import app_settings
from pulse_hwm.alerts.notifier import AlertChannels
from pulse_hwm.app_settings import AppSettings
from pulse_hwm.db import Database
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel


class SettingsTab(QWidget):
    def __init__(self, db: Database, sites_monitor, alerts, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self._db = db
        self._monitor = sites_monitor
        self._alerts = alerts

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
            'Temp sensors via LibreHardwareMonitorLib 0.9.6 (MPL-2.0) — '
            'librehardwaremonitor.org — see THIRD_PARTY.md'
        )
        credit.setObjectName("muted")
        credit.setWordWrap(True)
        data_panel.body().addWidget(credit)
        outer.addWidget(data_panel)

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
        )

    def _apply(self) -> None:
        values = self.collect()
        app_settings.save(self._db, values)
        self._monitor.reconfigure(
            interval_s=values.website_interval_s,
            timeout_s=values.website_timeout_s,
            ssl_warn_days=values.ssl_warn_days,
        )
        self._alerts.set_channels(AlertChannels(
            sound=values.sound_enabled,
            desktop=values.desktop_enabled,
            webhooks=values.webhooks_enabled,
        ))
        self._prune(values.retention_days)

    def _test_alert(self) -> None:
        self._alerts.notify(
            "info", "PULSE-HWM TEST", "This is what an alert looks like.", play_sound=True,
        )
