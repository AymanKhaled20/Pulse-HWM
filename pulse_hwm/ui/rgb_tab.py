"""RGB tab — control surface for the lighting engine.

Layout, top to bottom:
  * RGB STATUS   — one honest line (LIVE · 30 FPS · 2/2 DEVICES OK, or what
                   is wrong), the global BRIGHTNESS slider and RE-SEND.
  * CONTROL MODE — four buttons (OFF / EFFECTS / REACTIVE / OVERRIDE). Only
                   the active mode's controls are shown, so every visible
                   control changes the lights right now.
  * LIVE PREVIEW — per device, the colors Pulse is actually sending (from
                   the worker's status report), so each click is visibly
                   confirmed even before the hardware updates.

All persistence rides the app_settings.save_field instant-apply contract:
every edit saves one field and asks the manager to re-plan. Qt only in this
file; decisions live in the Qt-free manager/planner layers.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm import app_settings
from pulse_hwm.db import Database
from pulse_hwm.rgb import assignment_store
from pulse_hwm.rgb.effects.base import EffectContext
from pulse_hwm.rgb.manager import REACTIVE_TEMP_EFFECT_ID, ModePlanner
from pulse_hwm.rgb.model import RgbDevice
from pulse_hwm.rgb.sensors import sensors_from_snapshot
from pulse_hwm.ui.widgets.led_strip import LedStrip
from pulse_hwm.ui.widgets.param_editor import ColorSwatchButton, ParamEditor
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel

# (mode id, button label, one-line explanation shown under the buttons)
MODES = (
    ("off", "OFF", "Pulse leaves your lights alone; vendor software stays in charge."),
    ("effects", "EFFECTS", "Pick an effect and colors for each device."),
    ("reactive", "REACTIVE", "Colors follow your temperatures and flash on alerts."),
    (
        "override",
        "OVERRIDE",
        "One look on every device, re-applied so vendor apps can't take over.",
    ),
)
MODE_IDS = tuple(mode_id for mode_id, _label, _text in MODES)

# rgb_reactive_source value → (dropdown label, sensor key, unit)
REACTIVE_SOURCES = (
    ("cpu", "CPU TEMPERATURE", "cpu_temp", "°C"),
    ("gpu", "GPU TEMPERATURE", "gpu_temp", "°C"),
    ("max_temp", "HOTTEST OF CPU / GPU", "max_temp", "°C"),
    ("mem", "MEMORY USE", "mem_pct", "%"),
)

# only these override params are stored (rgb_override_color / _speed)
_OVERRIDE_KEYS = ("color", "speed")
_BRIGHTNESS_SAVE_MS = 300  # save the slider once it stops moving
_FEEDBACK_MS = 3000  # how long "RE-SENT TO …" stays visible


@dataclass
class _DeviceRow:
    """Widgets of one device in the EFFECTS page."""

    checkbox: QCheckBox
    combo: QComboBox
    editor: ParamEditor


@dataclass
class _PreviewRow:
    """Widgets of one device in the LIVE PREVIEW panel."""

    strip: LedStrip
    state: QLabel


def _plain_label(text: str = "", object_name: str = "") -> QLabel:
    """QLabel that never interprets HTML. Device names, effect names and
    error messages come from OpenRGB / imported files, so they are always
    shown as plain text."""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if object_name:
        label.setObjectName(object_name)
    return label


class RgbTab(QWidget):
    mode_changed = Signal(str)  # validated mode id, after persistence

    def __init__(
        self,
        db: Database,
        manager=None,
        brightness_bridge=None,
        catalog=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("root")
        self._db = db
        self._manager = manager  # Qt-free RgbManager (may be None in tests)
        self._brightness_bridge = brightness_bridge  # fn(pct) → rgb worker
        # use the SAME catalog as the engine when there is one, so imported
        # effects work immediately (see RgbManager.catalog)
        self._catalog = catalog or getattr(manager, "catalog", None)
        if self._catalog is None:
            self._catalog = _standalone_catalog(db)

        # latest facts from the rgb thread, redrawn by _render_status()
        self._driver_id = ""
        self._driver_name = ""
        self._driver_error = ""
        self._seen_devices: list = []
        self._last_status: dict | None = None
        self._sensors: dict = {}
        self._device_rows: dict[str, _DeviceRow] = {}
        self._preview_rows: dict[str, _PreviewRow] = {}

        self._brightness_save = QTimer(self)
        self._brightness_save.setSingleShot(True)
        self._brightness_save.setInterval(_BRIGHTNESS_SAVE_MS)
        self._brightness_save.timeout.connect(self._save_brightness)
        self._feedback_clear = QTimer(self)
        self._feedback_clear.setSingleShot(True)
        self._feedback_clear.setInterval(_FEEDBACK_MS)
        self._feedback_clear.timeout.connect(lambda: self._feedback.setText(""))

        # the page can grow taller than a laptop window: scroll it
        content = QWidget()
        content.setObjectName("root")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(self._build_status_panel())
        layout.addWidget(self._build_mode_panel())
        layout.addWidget(self._build_preview_panel())
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.load_settings()

    # ── building ────────────────────────────────────────────────────────
    def _build_status_panel(self) -> PixelPanel:
        panel = PixelPanel("RGB STATUS")
        self._status_line = _plain_label("STARTING…")
        self._status_detail = _plain_label("DRIVER: probing…", "muted")
        self._errors_label = _plain_label("", "rgbProblem")
        self._errors_label.hide()

        # RE-SEND replaces the old APPLY NOW + RECONSIDER NOW pair: one
        # button, and it always says what it did
        self._resend_btn = QPushButton("RE-SEND")
        self._resend_btn.setToolTip(
            "Push the current look to every device again (use this if another "
            "app changed your lights)"
        )
        self._resend_btn.clicked.connect(self._resend)
        self._feedback = _plain_label("", "muted")

        top_row = QHBoxLayout()
        top_row.addWidget(self._status_line, 1)
        top_row.addWidget(self._resend_btn)

        self._brightness = QSlider(Qt.Orientation.Horizontal)
        self._brightness.setRange(0, 100)
        self._brightness.valueChanged.connect(self._on_brightness_moved)
        self._brightness_value = QLabel("100 %")
        self._brightness_value.setMinimumWidth(48)
        brightness_row = QHBoxLayout()
        brightness_row.addWidget(QLabel("BRIGHTNESS"))
        brightness_row.addWidget(self._brightness, 1)
        brightness_row.addWidget(self._brightness_value)

        body = panel.body()
        body.addLayout(top_row)
        body.addWidget(self._status_detail)
        body.addWidget(self._errors_label)
        body.addWidget(self._feedback)
        body.addLayout(brightness_row)
        return panel

    def _build_mode_panel(self) -> PixelPanel:
        panel = PixelPanel("CONTROL MODE")
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_buttons: dict[str, QPushButton] = {}
        buttons_row = QHBoxLayout()
        for index, (mode_id, label, _text) in enumerate(MODES):
            button = QPushButton(label)
            button.setObjectName("modeButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._mode_group.addButton(button, index)
            self._mode_buttons[mode_id] = button
            buttons_row.addWidget(button)
        buttons_row.addStretch(1)
        self._mode_group.idClicked.connect(self._on_mode_clicked)
        self._mode_text = _plain_label("", "muted")

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_off_page())
        self._pages.addWidget(self._build_effects_page())
        self._pages.addWidget(self._build_reactive_page())
        self._pages.addWidget(self._build_override_page())

        body = panel.body()
        body.addLayout(buttons_row)
        body.addWidget(self._mode_text)
        body.addWidget(self._pages)
        return panel

    def _build_off_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.addWidget(
            _plain_label(
                "Pulse is not sending anything to your devices. Pick EFFECTS, "
                "REACTIVE or OVERRIDE above to take control.",
                "muted",
            )
        )
        layout.addStretch(1)
        return page

    def _build_effects_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        self._effects_empty = _plain_label(
            "No devices found yet. Is OpenRGB enabled and your device plugged in?",
            "muted",
        )
        layout.addWidget(self._effects_empty)
        self._device_rows_layout = QVBoxLayout()
        self._device_rows_layout.setSpacing(10)
        layout.addLayout(self._device_rows_layout)
        layout.addWidget(self._build_import_panel())
        return page

    def _build_import_panel(self) -> QWidget:
        # rarely used, so it starts folded away behind one button
        self._import_toggle = QPushButton("IMPORT MORE EFFECTS ▸")
        self._import_toggle.setCheckable(True)
        self._import_toggle.toggled.connect(self._on_import_toggled)
        panel = PixelPanel()
        panel.hide()
        self._import_panel = panel
        self._import_file_btn = QPushButton("FROM FILE")
        self._import_file_btn.clicked.connect(self._import_effect_file)
        self._import_paste_btn = QPushButton("PASTE JSON")
        self._import_paste_btn.clicked.connect(self._import_paste)
        self._import_url_btn = QPushButton("FROM URL")
        self._import_url_btn.setToolTip("HTTPS only — turn on the toggle below first")
        self._import_url_btn.clicked.connect(self._import_url)
        row = QHBoxLayout()
        row.addWidget(self._import_file_btn)
        row.addWidget(self._import_paste_btn)
        row.addWidget(self._import_url_btn)
        row.addStretch(1)
        row_widget = QWidget()
        row_widget.setLayout(row)
        panel.add_body(row_widget)
        panel.add_body(
            _plain_label(
                "Imports are validated (JSON only, 20 KB, whitelisted operations).",
                "muted",
            )
        )
        self._url_gate = QCheckBox("ALLOW EFFECT URLS (fetch .json via HTTPS)")
        self._url_gate.setToolTip(
            "Off by default: network content only loads when you ask for it here"
        )
        self._url_gate.clicked.connect(self._on_url_gate_clicked)
        panel.add_body(self._url_gate)
        self._import_result = _plain_label("", "muted")
        panel.add_body(self._import_result)
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(self._import_toggle)
        toggle_row.addStretch(1)
        wrapper_layout.addLayout(toggle_row)
        wrapper_layout.addWidget(panel)
        return wrapper

    def _on_import_toggled(self, shown: bool) -> None:
        self._import_panel.setVisible(shown)
        self._import_toggle.setText(
            "IMPORT MORE EFFECTS ▾" if shown else "IMPORT MORE EFFECTS ▸"
        )

    def _build_reactive_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 6, 0, 0)
        # short numbers and color chips: no need to span the whole row
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self._reactive_source = QComboBox()
        for source_id, label, _key, _unit in REACTIVE_SOURCES:
            self._reactive_source.addItem(label, source_id)
        self._reactive_source.currentIndexChanged.connect(self._on_reactive_source)
        self._reactive_low = QSpinBox()
        self._reactive_low.setRange(0, 100)
        self._reactive_low.valueChanged.connect(self._on_reactive_range)
        self._reactive_high = QSpinBox()
        self._reactive_high.setRange(1, 150)
        self._reactive_high.valueChanged.connect(self._on_reactive_range)
        self._reactive_cool = ColorSwatchButton()
        self._reactive_cool.color_changed.connect(
            lambda value: self._save_and_replan("rgb_temp_low_color", value)
        )
        self._reactive_hot = ColorSwatchButton()
        self._reactive_hot.color_changed.connect(
            lambda value: self._save_and_replan("rgb_temp_high_color", value)
        )
        self._alert_color = ColorSwatchButton()
        self._alert_color.color_changed.connect(
            lambda value: self._save_and_replan("rgb_alert_color", value)
        )
        self._alert_hold = QDoubleSpinBox()
        self._alert_hold.setRange(0.5, 30.0)
        self._alert_hold.setSingleStep(0.5)
        self._alert_hold.setSuffix(" s")
        self._alert_hold.valueChanged.connect(self._on_alert_hold)
        self._reactive_now = _plain_label("NOW: waiting for sensor data…", "muted")
        self._reactive_now_strip = LedStrip()

        form.addRow("FOLLOW", self._reactive_source)
        form.addRow("COOL AT", self._reactive_low)
        form.addRow("HOT AT", self._reactive_high)
        form.addRow("COOL COLOR", self._reactive_cool)
        form.addRow("HOT COLOR", self._reactive_hot)
        form.addRow("ALERT FLASH", self._alert_color)
        form.addRow("FLASH FOR", self._alert_hold)
        form.addRow(self._reactive_now)
        form.addRow(self._reactive_now_strip)
        return page

    def _build_override_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 6, 0, 0)
        self._override_effect = QComboBox()
        self._fill_effect_combo(self._override_effect)
        self._override_effect.currentIndexChanged.connect(self._on_override_effect)
        self._override_editor = ParamEditor()
        self._override_editor.params_changed.connect(self._on_override_params)
        form.addRow("EFFECT", self._override_effect)
        form.addRow(self._override_editor)
        return page

    def _build_preview_panel(self) -> PixelPanel:
        panel = PixelPanel("LIVE PREVIEW — WHAT PULSE IS SENDING")
        self._preview_empty = _plain_label("No devices.", "muted")
        panel.add_body(self._preview_empty)
        self._preview_form = QFormLayout()
        panel.body().addLayout(self._preview_form)
        return panel

    def _fill_effect_combo(self, combo: QComboBox) -> None:
        combo.blockSignals(True)
        current = combo.currentData()
        combo.clear()
        for effect in self._catalog.selectable():
            combo.addItem(effect.name, effect.effect_id)
        if current is not None:
            combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.blockSignals(False)

    # ── population ──────────────────────────────────────────────────────
    def load_settings(self) -> None:
        """Fill every control from the stored settings WITHOUT firing the
        change handlers (same pattern as SettingsTab.load_from)."""
        values = app_settings.load(self._db)
        self._show_mode(values.rgb_mode)

        self._brightness.blockSignals(True)
        self._brightness.setValue(values.rgb_brightness)
        self._brightness.blockSignals(False)
        self._brightness_value.setText(f"{values.rgb_brightness} %")

        self._fill_effect_combo(self._override_effect)
        self._override_effect.blockSignals(True)
        self._override_effect.setCurrentIndex(
            max(0, self._override_effect.findData(values.rgb_override_effect))
        )
        self._override_effect.blockSignals(False)
        self._rebuild_override_editor(values)

        for widget, value in (
            (self._reactive_low, values.rgb_temp_low_c),
            (self._reactive_high, values.rgb_temp_high_c),
            (self._alert_hold, values.rgb_alert_hold_ms / 1000.0),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._reactive_source.blockSignals(True)
        self._reactive_source.setCurrentIndex(
            max(0, self._reactive_source.findData(values.rgb_reactive_source))
        )
        self._reactive_source.blockSignals(False)
        # set_hex never emits, so no blocking needed for the swatches
        self._reactive_cool.set_hex(values.rgb_temp_low_color)
        self._reactive_hot.set_hex(values.rgb_temp_high_color)
        self._alert_color.set_hex(values.rgb_alert_color)

        self._url_gate.blockSignals(True)
        self._url_gate.setChecked(values.rgb_allow_effect_urls)
        self._url_gate.blockSignals(False)
        self._import_url_btn.setEnabled(values.rgb_allow_effect_urls)
        self._update_reactive_readout()

    def _show_mode(self, mode_id: str) -> None:
        mode_id = mode_id if mode_id in MODE_IDS else "off"
        index = MODE_IDS.index(mode_id)
        self._mode_buttons[mode_id].setChecked(True)
        self._pages.setCurrentIndex(index)
        # a QStackedWidget is as tall as its TALLEST page; letting hidden
        # pages ignore sizing makes it fit the page actually shown
        for page_index in range(self._pages.count()):
            policy = (
                QSizePolicy.Policy.Preferred
                if page_index == index
                else QSizePolicy.Policy.Ignored
            )
            self._pages.widget(page_index).setSizePolicy(policy, policy)
        self._pages.updateGeometry()
        self._mode_text.setText(MODES[index][2])
        self._mode = mode_id
        self._render_status()

    def _rebuild_override_editor(self, values=None) -> None:
        values = values or app_settings.load(self._db)
        effect = self._catalog.get(str(self._override_effect.currentData() or ""))
        self._override_editor.set_effect(
            effect,
            {
                "color": values.rgb_override_color,
                "speed": values.rgb_override_speed / 100.0,
            },
            only_keys=_OVERRIDE_KEYS,
        )

    # ── slots fed from the rgb thread / hardware collector ─────────────
    @Slot(str, str, list)
    def show_driver(self, driver_id: str, name: str, devices: list) -> None:
        self._driver_id = str(driver_id)
        self._driver_name = str(name)
        self._seen_devices = list(devices) if driver_id else []
        if driver_id:
            self._driver_error = ""  # attached fine: an old failure is over
        self._rebuild_device_rows(self._seen_devices)
        self._rebuild_preview_rows(self._seen_devices)
        self._render_status()

    @Slot(str)
    def show_error(self, message: str) -> None:
        """Driver attach/probe failure (per-frame problems arrive through
        show_status instead)."""
        self._driver_error = str(message)
        self._render_status()

    @Slot(dict)
    def show_status(self, status: dict) -> None:
        """Worker's ~5/s report: preview frames, fps, per-device errors."""
        self._last_status = dict(status or {})
        frames = self._last_status.get("frames") or {}
        errors = self._last_status.get("errors") or {}
        for device_id, row in self._preview_rows.items():
            row.strip.set_colors(frames.get(device_id, []))
            if device_id in errors:
                row.state.setObjectName("rgbProblem")
                row.state.setText(str(errors[device_id]))
            elif device_id in frames:
                row.state.setObjectName("muted")
                row.state.setText("")
            else:
                row.state.setObjectName("muted")
                row.state.setText("not driven by Pulse")
            row.state.style().polish(row.state)  # objectName changed → restyle
        self._render_status()

    @Slot(dict)
    def show_sensors(self, snapshot: dict) -> None:
        self._sensors = sensors_from_snapshot(snapshot or {})
        if self._mode == "reactive":
            self._update_reactive_readout()

    # ── status line ─────────────────────────────────────────────────────
    def _render_status(self) -> None:
        mode = getattr(self, "_mode", "off")
        status = self._last_status or {}
        errors = status.get("errors") or {}
        driven = int(status.get("driven") or 0)
        ok = int(status.get("ok") or 0)
        if not self._driver_id:
            text, look = "NO RGB DRIVER", "rgbProblem"
            detail = self._driver_error or (
                "OpenRGB isn't running, or it is turned off in settings."
            )
        else:
            detail = (
                f"DRIVER: {self._driver_name} · {len(self._seen_devices)} DEVICES FOUND"
            )
            if mode == "off":
                text, look = "OFF — Pulse is not controlling your lights", "muted"
            elif driven == 0 and mode == "effects":
                text, look = (
                    "IDLE — no device has an effect yet (tick one below)",
                    "muted",
                )
            elif driven == 0:
                text, look = "WAITING FOR THE FIRST FRAME…", "muted"
            elif errors:
                text, look = f"PROBLEM · {ok}/{driven} DEVICES OK", "rgbProblem"
            else:
                fps = float(status.get("fps") or 0.0)
                text = f"LIVE · {fps:.0f} FPS · {ok}/{driven} DEVICES OK"
                look = "rgbLive"
        self._status_line.setText(text)
        if self._status_line.objectName() != look:
            self._status_line.setObjectName(look)
            self._status_line.style().polish(self._status_line)
        self._status_detail.setText(detail)

        names = {d.device_id: d.name for d in self._seen_devices}
        # "*" (a whole-engine failure) isn't a device name, so it reads "engine"
        lines = [
            f"{names.get(device_id, 'engine')}: {message}"
            for device_id, message in errors.items()
        ]
        self._errors_label.setText("\n".join(lines))
        self._errors_label.setVisible(bool(lines) and mode != "off")

    # ── handlers: mode, brightness, re-send ─────────────────────────────
    def _on_mode_clicked(self, index: int) -> None:
        mode_id = MODE_IDS[index]
        # clicked path == instant-apply contract: persist the single field
        app_settings.save_field(self._db, "rgb_mode", mode_id)
        self._show_mode(mode_id)
        if mode_id == "reactive":
            self._update_reactive_readout()
        self.mode_changed.emit(mode_id)
        self._reconsider()

    def _on_brightness_moved(self, value: int) -> None:
        self._brightness_value.setText(f"{value} %")
        # the worker gets every step (it only rescales the next frames);
        # the settings write waits until the slider stops moving
        if self._brightness_bridge is not None:
            self._brightness_bridge(int(value))
        self._brightness_save.start()

    def _save_brightness(self) -> None:
        app_settings.save_field(
            self._db, "rgb_brightness", int(self._brightness.value())
        )

    def _resend(self) -> None:
        if self._manager is None:
            return
        if self._mode == "off":
            self._show_feedback("Nothing to send: mode is OFF.")
            return
        self._manager.force_reconsider()
        count = len(self._seen_devices)
        self._show_feedback(
            f"RE-SENT TO {count} DEVICE{'S' if count != 1 else ''}"
            if count
            else "No devices to send to."
        )

    def _show_feedback(self, text: str) -> None:
        self._feedback.setText(text)
        self._feedback_clear.start()

    # ── handlers: EFFECTS page (per-device rows) ────────────────────────
    def _rebuild_device_rows(self, devices: list) -> None:
        _clear_layout(self._device_rows_layout)
        self._device_rows.clear()
        self._effects_empty.setVisible(not devices)
        blob = app_settings.load(self._db).rgb_device_assignment
        for device in devices:
            entry = assignment_store.entry_of(blob, self._driver_id, device.device_id)
            checkbox = QCheckBox(f"{device.name} ({device.leds} LEDs)")
            checkbox.setToolTip(device.device_id)
            checkbox.setChecked(entry is not None)
            combo = QComboBox()
            self._fill_effect_combo(combo)
            if entry is not None:
                combo.setCurrentIndex(max(0, combo.findData(entry.get("effect"))))
            editor = ParamEditor()
            params = entry.get("params") if entry else {}
            editor.set_effect(
                self._catalog.get(str(combo.currentData() or "")),
                params if isinstance(params, dict) else {},
            )
            editor.setEnabled(entry is not None)

            did = device.device_id
            checkbox.clicked.connect(
                lambda on, did=did: self._on_assignment_toggled(did, on)
            )
            combo.currentIndexChanged.connect(
                lambda _i, did=did: self._on_effect_picked(did)
            )
            editor.params_changed.connect(
                lambda values, did=did: self._on_device_params(did, values)
            )

            header = QHBoxLayout()
            header.addWidget(checkbox, 1)
            header.addWidget(combo)
            box = QFrame()
            box.setObjectName("pixelPanel")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(8, 6, 8, 6)
            box_layout.addLayout(header)
            box_layout.addWidget(editor)
            self._device_rows_layout.addWidget(box)
            self._device_rows[did] = _DeviceRow(checkbox, combo, editor)

    def _on_assignment_toggled(self, device_id: str, on: bool) -> None:
        row = self._device_rows.get(device_id)
        if row is None:
            return
        row.editor.setEnabled(bool(on))
        if on:
            # save the effect together with what the editor currently shows,
            # so the lights match the screen immediately
            self._upsert_assignment(
                device_id, self._row_effect(row), row.editor.values()
            )
        else:
            self._remove_assignment(device_id)

    def _on_effect_picked(self, device_id: str) -> None:
        row = self._device_rows.get(device_id)
        if row is None:
            return
        # new effect → new controls; stored params (e.g. the color) carry over
        entry = assignment_store.entry_of(
            app_settings.load(self._db).rgb_device_assignment,
            self._driver_id,
            device_id,
        )
        stored = entry.get("params") if entry else {}
        stored = stored if isinstance(stored, dict) else {}
        row.editor.set_effect(self._catalog.get(self._row_effect(row)), stored)
        # picking an effect implies enabling the device
        if not row.checkbox.isChecked():
            row.checkbox.setChecked(True)
            row.editor.setEnabled(True)
        self._upsert_assignment(
            device_id, self._row_effect(row), {**stored, **row.editor.values()}
        )

    def _on_device_params(self, device_id: str, values: dict) -> None:
        row = self._device_rows.get(device_id)
        if row is None or not row.checkbox.isChecked():
            return
        self._upsert_assignment(device_id, self._row_effect(row), values)

    @staticmethod
    def _row_effect(row: _DeviceRow) -> str:
        return str(row.combo.currentData() or "static")

    def _remove_assignment(self, device_id: str) -> None:
        blob = assignment_store.clear(
            app_settings.load(self._db).rgb_device_assignment,
            self._driver_id,
            device_id,
        )
        app_settings.save_field(self._db, "rgb_device_assignment", blob)
        self._reconsider()

    def _upsert_assignment(self, device_id: str, effect_id: str, params: dict) -> None:
        blob = assignment_store.assign(
            app_settings.load(self._db).rgb_device_assignment,
            self._driver_id,
            device_id,
            effect_id,
            True,
            params=params,
        )
        app_settings.save_field(self._db, "rgb_device_assignment", blob)
        self._reconsider()

    # ── handlers: REACTIVE page ─────────────────────────────────────────
    def _on_reactive_source(self, _index: int) -> None:
        self._save_and_replan(
            "rgb_reactive_source", str(self._reactive_source.currentData())
        )

    def _on_reactive_range(self, _value: int) -> None:
        low = self._reactive_low.value()
        high = self._reactive_high.value()
        if high <= low:
            # keep HOT above COOL: an inverted range would map every value
            # to one end and look like the setting did nothing
            self._reactive_high.blockSignals(True)
            self._reactive_high.setValue(low + 1)
            self._reactive_high.blockSignals(False)
            high = low + 1
        app_settings.save_field(self._db, "rgb_temp_low_c", int(low))
        app_settings.save_field(self._db, "rgb_temp_high_c", int(high))
        self._update_reactive_readout()
        self._reconsider()

    def _on_alert_hold(self, seconds: float) -> None:
        app_settings.save_field(
            self._db, "rgb_alert_hold_ms", int(round(seconds * 1000))
        )

    def _save_and_replan(self, key: str, value) -> None:
        app_settings.save_field(self._db, key, value)
        self._update_reactive_readout()
        self._reconsider()

    def _update_reactive_readout(self) -> None:
        """Show what REACTIVE would paint right now, computed by the real
        reactive effect so the screen and the LEDs can't disagree."""
        values = app_settings.load(self._db)
        source = next(
            (s for s in REACTIVE_SOURCES if s[0] == values.rgb_reactive_source),
            REACTIVE_SOURCES[0],
        )
        _sid, label, sensor_key, unit = source
        reading = self._sensors.get(sensor_key)
        effect = self._catalog.get(REACTIVE_TEMP_EFFECT_ID)
        if reading is None or effect is None:
            self._reactive_now.setText(f"NOW: no {label.lower()} reading yet")
            self._reactive_now_strip.set_colors([])
            return
        planner = ModePlanner(
            mode="reactive",
            driver_id="",
            device_ids=[],
            catalog=self._catalog,
            override_effect_id="",
            override_color="",
            reactive_sensor=values.rgb_reactive_source,
            reactive_low_c=values.rgb_temp_low_c,
            reactive_high_c=values.rgb_temp_high_c,
            reactive_low_color=values.rgb_temp_low_color,
            reactive_high_color=values.rgb_temp_high_color,
        )
        context = EffectContext(
            device=RgbDevice("preview", "preview", "preview", leds=1),
            params=planner.reactive_params(),
            sensors=self._sensors,
        )
        color = effect.render(context)[0].to_hex()
        self._reactive_now.setText(f"NOW: {label} {reading:.0f} {unit} → {color}")
        self._reactive_now_strip.set_colors([color] * 16)

    # ── handlers: OVERRIDE page ─────────────────────────────────────────
    def _on_override_effect(self, _index: int) -> None:
        effect_id = str(self._override_effect.currentData() or "static")
        app_settings.save_field(self._db, "rgb_override_effect", effect_id)
        self._rebuild_override_editor()
        self._reconsider()

    def _on_override_params(self, values: dict) -> None:
        if "color" in values:
            app_settings.save_field(
                self._db, "rgb_override_color", str(values["color"])
            )
        if "speed" in values:
            # effect speed is 0.05..1.0; the setting stores 0..100
            speed = int(round(float(values["speed"]) * 100))
            app_settings.save_field(self._db, "rgb_override_speed", speed)
        self._reconsider()

    # ── preview rows ────────────────────────────────────────────────────
    def _rebuild_preview_rows(self, devices: list) -> None:
        while self._preview_form.rowCount():
            self._preview_form.removeRow(0)
        self._preview_rows.clear()
        self._preview_empty.setVisible(not devices)
        for device in devices:
            strip = LedStrip()
            state = _plain_label("not driven by Pulse", "muted")
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(strip)
            cell.addWidget(state)
            name = _plain_label(device.name)
            name.setToolTip(device.device_id)
            self._preview_form.addRow(name, cell)
            self._preview_rows[device.device_id] = _PreviewRow(strip, state)

    # ── misc ────────────────────────────────────────────────────────────
    def _on_url_gate_clicked(self, on: bool) -> None:
        app_settings.save_field(self._db, "rgb_allow_effect_urls", bool(on))
        self._import_url_btn.setEnabled(bool(on))

    def _reconsider(self) -> None:
        if self._manager is not None:
            self._manager.reconsider()

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
            self._import_result.setText(f"import failed: {exc}")
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
            self._import_result.setText("import rejected: " + "; ".join(errors))
            return
        store = UserEffectStore(self._db)
        ok, reason = store.add(definition)
        if not ok:
            self._import_result.setText(reason)
            return
        store.register_with_catalog(self._catalog)
        self._import_result.setText(f"imported effect {definition['id']}")
        self._after_import()

    def _import_url(self) -> None:
        # the gate is real: rgb_allow_effect_urls defaults off
        values = app_settings.load(self._db)
        if not values.rgb_allow_effect_urls:
            self._import_result.setText(
                "URL import is off — tick ALLOW EFFECT URLS first"
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
            self._import_result.setText("URL import rejected: " + "; ".join(errors))
            return
        store = UserEffectStore(self._db)
        ok, reason = store.add(definition)
        if ok:
            store.register_with_catalog(self._catalog)
        self._import_result.setText(
            f"imported effect {definition['id']} from {fetcher.last_url}"
            if ok
            else reason
        )
        if ok:
            self._after_import()

    def _after_import(self) -> None:
        """New effect → it must show up in every effect dropdown."""
        self._fill_effect_combo(self._override_effect)
        self._rebuild_device_rows(self._seen_devices)


def _standalone_catalog(db: Database):
    """Catalog for a tab built without a manager (tests): built-ins plus the
    user's imported effects, like the controller builds its own."""
    from pulse_hwm.rgb.effects.catalog import EffectCatalog
    from pulse_hwm.rgb.effects.loader import UserEffectStore

    catalog = EffectCatalog()
    UserEffectStore(db).register_with_catalog(catalog)
    return catalog


def _clear_layout(layout) -> None:
    """Remove and delete every widget in `layout` (rows get rebuilt when the
    device list changes)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
