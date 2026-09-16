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
    QCheckBox,
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
from pulse_hwm.rgb import assignment_store
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
        # the explicit apply the user asked for: mode/assignment changes are
        # instant-apply by contract, but this button FORCES a re-push even
        # when the stored plan looks unchanged — one predictable way to
        # "make the lights match what's on screen right now"
        self._apply_btn = QPushButton("APPLY NOW")
        self._apply_btn.setToolTip(
            "Force the current mode/assignments onto the hardware, even if "
            "nothing looks changed"
        )
        self._apply_btn.clicked.connect(self._apply_now)
        apply_row = QHBoxLayout()
        apply_row.addWidget(self._apply_btn)
        apply_row.addStretch(1)
        body.addLayout(apply_row)
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

        self._devices_panel = PixelPanel("DEVICES + ASSIGNED EFFECTS")
        self._devices_label = QLabel("")
        self._devices_label.setObjectName("muted")
        self._devices_panel.body().addWidget(self._devices_label)
        self._device_rows: dict[str, tuple] = {}  # device_id → (checkbox, combo)
        self._devices_grid = QFormLayout()
        self._catalog = None  # filled when devices list (with driver) lands
        self._current_driver_id = ""
        self._seen_devices: list = []
        self._devices_panel.body().addLayout(self._devices_grid)
        self._assignment_hint = QLabel(
            "Assignments apply when CONTROL MODE is EFFECTS."
        )
        self._assignment_hint.setObjectName("muted")
        self._assignment_hint.setWordWrap(True)
        self._devices_panel.body().addWidget(self._assignment_hint)
        self._import_file_btn = QPushButton("IMPORT EFFECT (FILE)")
        self._import_file_btn.clicked.connect(self._import_effect_file)
        self._import_paste_btn = QPushButton("IMPORT EFFECT (PASTE)")
        self._import_paste_btn.clicked.connect(self._import_paste)
        self._import_url_btn = QPushButton("IMPORT EFFECT (URL)")
        self._import_url_btn.setToolTip("HTTPS only — must be enabled in settings")
        self._import_url_btn.clicked.connect(self._import_url)
        import_row = QHBoxLayout()
        import_row.addWidget(self._import_file_btn)
        import_row.addWidget(self._import_paste_btn)
        import_row.addWidget(self._import_url_btn)
        import_row.addStretch(1)
        self._devices_panel.body().addLayout(import_row)
        self._import_hint = QLabel(
            "External imports are validated (JSON only, 20 KB, whitelisted ops). "
            "URL fetch additionally requires the toggle below."
        )
        self._import_hint.setObjectName("muted")
        self._import_hint.setWordWrap(True)
        self._devices_panel.body().addWidget(self._import_hint)
        self._url_gate = QCheckBox("ALLOW EFFECT URLS (fetch .json via HTTPS)")
        self._url_gate.setToolTip(
            "Off by default: network content only loads when you ask for it here"
        )
        self._url_gate.clicked.connect(self._on_url_gate_clicked)
        self._devices_panel.body().addWidget(self._url_gate)
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
        self._url_gate.blockSignals(True)
        self._url_gate.setChecked(values.rgb_allow_effect_urls)
        self._url_gate.blockSignals(False)
        self._import_url_btn.setEnabled(values.rgb_allow_effect_urls)

    def show_driver(self, driver_id: str, name: str, devices: list) -> None:
        self._seen_devices = list(devices)
        self._current_driver_id = str(driver_id)
        if not driver_id:
            self._status_driver.setText("DRIVER: none attached")
            self._status_devices.setText("DEVICES: 0")
            self._rebuild_rows([])
            return
        self._status_driver.setText(f"DRIVER: {name}")
        summary = ", ".join(
            f"{d.device_id} ({getattr(d, 'leds', '?')} LEDs)" for d in devices
        )
        self._status_devices.setText(f"DEVICES: {len(devices)}")
        self._devices_label.setText(summary or "no devices reported")
        self._rebuild_rows(devices)

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

    # ── per-device assignment rows (phase 11) ───────────────────────────
    def _rebuild_rows(self, devices: list) -> None:
        while self._devices_grid.rowCount():
            self._devices_grid.removeRow(self._devices_grid.rowCount() - 1)
        self._device_rows.clear()
        if devices and self._catalog is None:
            self._catalog = self._load_catalog()
        blob = app_settings.load(self._db).rgb_device_assignment
        for device in devices:
            combo = QComboBox()
            for effect in self._catalog.all():
                combo.addItem(effect.name, effect.effect_id)
            entry = assignment_store.entry_of(
                blob, self._current_driver_id, device.device_id
            )
            checkbox = QCheckBox(f"{device.device_id} — {device.name}")
            checkbox.setChecked(entry is not None)
            combo.setEnabled(entry is not None)
            if entry is not None:
                index = combo.findData(entry["effect"])
                combo.setCurrentIndex(max(0, index))
            checkbox.clicked.connect(
                lambda on, did=device.device_id: self._on_assignment_toggled(did, on)
            )
            combo.currentIndexChanged.connect(
                lambda _i, did=device.device_id: self._on_effect_picked(did)
            )
            self._devices_grid.addRow(checkbox, combo)
            self._device_rows[device.device_id] = (checkbox, combo)

    def _load_catalog(self):
        from pulse_hwm.rgb.effects.catalog import EffectCatalog

        return self._catalog or EffectCatalog()

    def _on_assignment_toggled(self, device_id: str, on: bool) -> None:
        # instant-apply: checkbox ON → upsert with the currently picked
        # effect; OFF → remove the row. Each change rewrites the blob and
        # triggers one planner pass.
        if on:
            self._upsert_assignment(device_id, self._combo_effect(device_id))
            combo = self._device_rows.get(device_id, (None, None))[1]
            if combo is not None:
                combo.setEnabled(True)
        else:
            self._remove_assignment(device_id)
            combo = self._device_rows.get(device_id, (None, None))[1]
            if combo is not None:
                combo.setEnabled(False)

    def _on_effect_picked(self, device_id: str) -> None:
        # picking an effect implies enabling the device
        checkbox = self._device_rows.get(device_id, (None, None))[0]
        if checkbox is not None and not checkbox.isChecked():
            checkbox.blockSignals(True)
            checkbox.setChecked(True)
            checkbox.blockSignals(False)
        self._upsert_assignment(device_id, self._combo_effect(device_id))

    def _combo_effect(self, device_id: str) -> str:
        combo = self._device_rows.get(device_id, (None, None))[1]
        if combo is None:
            return "static"
        return str(combo.currentData() or "static")

    def _remove_assignment(self, device_id: str) -> None:
        blob = assignment_store.clear(
            app_settings.load(self._db).rgb_device_assignment,
            self._current_driver_id,
            device_id,
        )
        app_settings.save_field(self._db, "rgb_device_assignment", blob)
        self._reconsider()

    def _upsert_assignment(self, device_id: str, effect_id: str) -> None:
        blob = assignment_store.assign(
            app_settings.load(self._db).rgb_device_assignment,
            self._current_driver_id,
            device_id,
            effect_id,
            True,
        )
        app_settings.save_field(self._db, "rgb_device_assignment", blob)
        self._reconsider()

    def _on_url_gate_clicked(self, on: bool) -> None:
        app_settings.save_field(self._db, "rgb_allow_effect_urls", bool(on))
        self._import_url_btn.setEnabled(bool(on))

    def _reconsider(self) -> None:
        if self._manager is not None:
            self._manager.reconsider()

    def _apply_now(self) -> None:
        # force = re-push even when the stored plan matches (user-visible
        # promise: the button always does something)
        if self._manager is not None:
            self._manager.force_reconsider()

    # ── effect import (phase 13) ────────────────────────────────────────
    def _import_effect_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _filter = QFileDialog.getOpenFileName(
            self, "IMPORT RGB EFFECT", "", "RGB Effect (*.json)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            self.show_error(f"import failed: {exc}")
            return
        self._import_raw(raw)

    def _import_paste(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        raw, ok = QInputDialog.getMultiLineText(
            self, "PASTE RGB EFFECT JSON", "Effect definition:", ""
        )
        if ok and raw.strip():
            self._import_raw(raw)

    def _import_raw(self, raw: str) -> None:
        from pulse_hwm.rgb.effects.loader import UserEffectStore, validate_definition

        definition, errors = validate_definition(raw)
        if definition is None:
            self.show_error("import rejected: " + "; ".join(errors))
            return
        store = UserEffectStore(self._db)
        ok, reason = store.add(definition)
        if not ok:
            self.show_error(reason)
            return
        if self._catalog is not None:
            store.register_with_catalog(self._catalog)
        self.load_settings()
        self.show_error(f"imported effect {definition['id']}")
        # re-populate effect dropdowns with the new definitions
        self._rebuild_rows(self._seen_devices)

    def _import_url(self) -> None:
        # the gate is real: rgb_allow_effect_urls defaults off
        values = app_settings.load(self._db)
        if not values.rgb_allow_effect_urls:
            self.show_error(
                "URL import disabled — turn it on with a settings toggle first"
            )
            return
        from PySide6.QtWidgets import QInputDialog

        url, ok = QInputDialog.getText(
            self, "FETCH RGB EFFECT", "HTTPS URL of a .json effect:"
        )
        if not (ok and url.strip()):
            return
        from pulse_hwm.rgb.effects.loader import URLFetcher, UserEffectStore

        fetcher = URLFetcher(self._db)
        try:
            definition, errors = fetcher.fetch(url.strip())
        finally:
            fetcher.close()
        if definition is None:
            self.show_error("URL import rejected: " + "; ".join(errors))
            return
        store = UserEffectStore(self._db)
        ok, reason = store.add(definition)
        if ok and self._catalog is not None:
            store.register_with_catalog(self._catalog)
        self.show_error(
            f"imported effect {definition['id']} from {fetcher.last_url}"
            if ok
            else reason
        )
        self._rebuild_rows(self._seen_devices)
