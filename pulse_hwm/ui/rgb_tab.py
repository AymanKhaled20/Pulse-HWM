"""RGB tab — control surface for the lighting engine.

Phase 8 ships the shell: STATUS strip (driver/mode/devices/errors), the
MODE selector (single enum, instant-apply), and a device list. Override +
assignment controls land in phases 10/11 in their own modules — this file
stays the composition root so later panels slot in without rework.

All persistence rides the app_settings.save_field instant-apply contract
(same as the ALERTS toggles). Qt only in this file; decisions live in the
Qt-free manager/planner layers.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm import app_settings
from pulse_hwm.db import Database
from pulse_hwm.ui.widgets.color_picker import ColorPicker
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel


class RgbTab(QWidget):
    mode_changed = Signal(str)  # validated mode id, after persistence

    def __init__(self, db: Database, manager=None, brightness_bridge=None, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self._db = db
        self._manager = manager  # Qt-free RgbManager (may be None in tests)
        self._brightness_bridge = brightness_bridge  # fn(pct) → worker, app.py

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self._status_panel = PixelPanel("RGB STATUS")
        self._status_driver = QLabel("DRIVER: probing…")
        self._status_mode = QLabel("MODE: —")
        self._status_devices = QLabel("DEVICES: —")
        self._status_error = QLabel("")
        self._status_error.setObjectName("muted")
        self._status_error.setWordWrap(True)
        status_form = QFormLayout()
        for label in (self._status_driver, self._status_mode, self._status_devices):
            label.setWordWrap(True)
            status_form.addRow(label)
        body = self._status_panel.body()
        body.addLayout(status_form)
        body.addWidget(self._status_error)
        layout.addWidget(self._status_panel)

        self._mode_panel = PixelPanel("CONTROL MODE")
        mode_form = QFormLayout()
        self._mode_box = QComboBox()
        for mode_id, label in (
            ("off", "OFF — leave devices to vendor software"),
            ("effects", "EFFECTS — run assigned effects"),
            ("reactive", "REACTIVE — temperature and alerts drive color"),
            ("override", "OVERRIDE — Pulse forces one look, beats vendor apps"),
        ):
            self._mode_box.addItem(label, mode_id)
        self._mode_box.currentIndexChanged.connect(self._on_mode_chosen)
        mode_form.addRow("DRIVES THE HARDWARE", self._mode_box)
        self._mode_panel.body().addLayout(mode_form)
        self._reconsider_btn = QPushButton("RECONSIDER NOW")
        self._reconsider_btn.setToolTip("Re-run mode resolution (usually automatic)")
        self._reconsider_btn.clicked.connect(self._reconsider)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._reconsider_btn)
        mode_row.addStretch(1)
        self._mode_panel.body().addLayout(mode_row)
        layout.addWidget(self._mode_panel)

        self._devices_panel = PixelPanel("DEVICES")
        self._devices_label = QLabel("")
        self._devices_label.setObjectName("muted")
        self._devices_panel.body().addWidget(self._devices_label)
        layout.addWidget(self._devices_panel, 1)

        # ── override controls (phase 10): instant-apply, same contract ──
        self._override_panel = PixelPanel("OVERRIDE LOOK")
        override_form = QFormLayout()
        self._picker = ColorPicker()
        self._picker.hex_chosen.connect(self._on_override_color)
        color_row = QHBoxLayout()
        color_row.addWidget(self._picker)
        color_row.addStretch(1)
        override_form.addRow("COLOR", color_row)
        self._brightness = QSpinBox()
        self._brightness.setRange(0, 100)
        self._brightness.setSuffix(" %")
        self._brightness.valueChanged.connect(self._on_override_value)
        self._speed = QSpinBox()
        self._speed.setRange(0, 100)
        self._speed.valueChanged.connect(self._on_override_value)
        override_form.addRow("BRIGHTNESS", self._brightness)
        override_form.addRow("SPEED", self._speed)
        self._override_panel.body().addLayout(override_form)
        self._override_note = QLabel(
            "OVERRIDE forces this look on every device and re-asserts it so "
            "Mystic Light / iCUE / Synapse cannot take the hardware back."
        )
        self._override_note.setObjectName("muted")
        self._override_note.setWordWrap(True)
        self._override_panel.body().addWidget(self._override_note)
        layout.addWidget(self._override_panel)

        layout.addStretch(1)
        self.load_settings()

    # ── population ──────────────────────────────────────────────────────
    def load_settings(self) -> None:
        values = app_settings.load(self._db)
        index = self._mode_box.findData(values.rgb_mode)
        self._mode_box.setCurrentIndex(max(0, index))
        self._status_mode.setText(f"MODE: {values.rgb_mode.upper()}")
        # override panel: blockSignals so the programmatic fill can't fire
        # the user-change handlers (same pattern as SettingsTab.load_from)
        self._brightness.blockSignals(True)
        self._brightness.setValue(values.rgb_brightness)
        self._brightness.blockSignals(False)
        self._speed.blockSignals(True)
        self._speed.setValue(values.rgb_override_speed)
        self._speed.blockSignals(False)
        self._picker.set_hex(values.rgb_override_color)

    def show_driver(self, driver_id: str, name: str, devices: list) -> None:
        if not driver_id:
            self._status_driver.setText("DRIVER: none attached")
            self._devices_label.setText("")
            return
        self._status_driver.setText(f"DRIVER: {name}")
        summary = ", ".join(
            f"{d.device_id} ({getattr(d, 'leds', '?')} LEDs)" for d in devices
        )
        self._status_devices.setText(f"DEVICES: {len(devices)}")
        self._devices_label.setText(summary or "no devices reported")

    def show_error(self, message: str) -> None:
        self._status_error.setText(message)

    # ── handlers ────────────────────────────────────────────────────────
    def _on_mode_chosen(self, index: int) -> None:
        mode_id = self._mode_box.itemData(index)
        if mode_id is None:
            return
        # clicked path == instant-apply contract: persist the single field
        app_settings.save_field(self._db, "rgb_mode", str(mode_id))
        self._status_mode.setText(f"MODE: {str(mode_id).upper()}")
        self._reconsider()

    def _on_override_color(self, hex_value: str) -> None:
        app_settings.save_field(self._db, "rgb_override_color", str(hex_value))
        self._reconsider()

    def _on_override_value(self) -> None:
        # brightness is the global engine scale; speed feeds the effect
        app_settings.save_field(
            self._db, "rgb_brightness", int(self._brightness.value())
        )
        app_settings.save_field(
            self._db, "rgb_override_speed", int(self._speed.value())
        )
        self._forward_brightness()
        self._reconsider()

    def _forward_brightness(self) -> None:
        if self._brightness_bridge is not None:
            self._brightness_bridge(int(self._brightness.value()))

    def _reconsider(self) -> None:
        if self._manager is not None:
            self._manager.reconsider()
