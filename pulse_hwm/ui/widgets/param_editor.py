"""Effect settings editor, built from the effect's own ParamSpecs.

Every effect already describes its parameters (key, label, kind, range,
default) in rgb/effects — this widget turns that description into controls,
so a new or imported effect gets a working COLOR/SPEED/… editor without any
RGB-tab code changes:

    color  → ColorSwatchButton (click → pixel color picker popup)
    float  → slider across the spec's minimum..maximum
    int    → spin box
    choice → dropdown

Changes are DEBOUNCED: dragging a slider or the color cursor fires dozens
of events a second, and each emit ends in a settings write + re-plan. The
editor waits until the input has been still for _DEBOUNCE_MS, then emits
`params_changed` once with ALL current values.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm.rgb.model import RgbColor
from pulse_hwm.ui.widgets.color_picker import ColorPicker

_DEBOUNCE_MS = 150
_SLIDER_STEPS = 100


class ColorSwatchButton(QPushButton):
    """A button filled with its color; clicking opens the pixel picker in a
    small popup under it. Emits color_changed("#RRGGBB") while picking."""

    color_changed = Signal(str)

    def __init__(self, hex_value: str = "#FFD400", parent=None):
        super().__init__(parent)
        self.setObjectName("colorSwatch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # a fixed-size chip: stretched across a whole form row it reads as
        # a banner instead of a button
        self.setFixedWidth(150)
        self._hex = "#FFD400"
        self._popup: QFrame | None = None  # the open picker, if any
        self.set_hex(hex_value)
        self.clicked.connect(self._open_picker)

    def hex(self) -> str:
        return self._hex

    def set_hex(self, value: str) -> None:
        # always normalized through RgbColor: the stylesheet below only ever
        # sees a clean "#RRGGBB", whatever the stored settings contained
        self._hex = RgbColor.from_hex(str(value)).to_hex()
        color = RgbColor.from_hex(self._hex)
        # dark text on light colors, light text on dark ones (readability)
        text = (
            "#000000"
            if (color.r * 299 + color.g * 587 + color.b * 114) > 128_000
            else "#FFFFFF"
        )
        self.setText(self._hex)
        self.setStyleSheet(
            f"QPushButton#colorSwatch {{ background: {self._hex}; color: {text}; }}"
        )

    def _open_picker(self) -> None:
        popup = QFrame(self, Qt.WindowType.Popup)
        popup.setObjectName("pixelPanel")
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(6, 6, 6, 6)
        picker = ColorPicker(popup)
        picker.set_hex(self._hex)
        picker.hex_chosen.connect(self._on_picked)
        layout.addWidget(picker)
        popup.adjustSize()
        popup.move(self.mapToGlobal(QPoint(0, self.height())))
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup.show()
        self._popup = popup  # closes itself on an outside click

    def _on_picked(self, value: str) -> None:
        self.set_hex(value)
        self.color_changed.emit(self._hex)


class ParamEditor(QWidget):
    params_changed = Signal(dict)  # all current values, after the debounce

    def __init__(self, parent=None):
        super().__init__(parent)
        self._form = QFormLayout(self)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._specs: tuple = ()
        # key → function returning that control's current value
        self._readers: dict = {}
        self._controls: dict = {}
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_now)

    # ── building ────────────────────────────────────────────────────────
    def set_effect(
        self, effect, values: dict | None = None, only_keys: tuple = ()
    ) -> None:
        """Rebuild the controls for `effect` (None = nothing to edit),
        filled from `values` (stored params; missing keys use the spec
        default). only_keys limits the editor to params the caller can
        actually save — showing a control that isn't stored anywhere would
        be exactly the "button that does nothing" this widget replaces.
        Building never emits params_changed."""
        self._debounce.stop()
        while self._form.rowCount():
            self._form.removeRow(0)
        self._readers.clear()
        self._controls.clear()
        self._specs = tuple(
            spec
            for spec in (getattr(effect, "params", ()) or ())
            if not only_keys or spec.key in only_keys
        )
        values = values or {}
        if not self._specs:
            note = QLabel("This effect has no settings.")
            note.setObjectName("muted")
            self._form.addRow(note)
            return
        for spec in self._specs:
            # stored values are untrusted (user-editable DB): validate first
            current = spec.validate(values.get(spec.key, spec.default))
            control, reader = self._build_control(spec, current)
            label = QLabel(spec.label)
            label.setTextFormat(Qt.TextFormat.PlainText)  # imported labels
            self._form.addRow(label, control)
            self._readers[spec.key] = reader
            self._controls[spec.key] = control

    def _build_control(self, spec, current):
        if spec.kind == "color":
            swatch = ColorSwatchButton(str(current))
            swatch.color_changed.connect(self._schedule)
            return swatch, swatch.hex
        if spec.kind == "float":
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, _SLIDER_STEPS)
            span = max(1e-9, spec.maximum - spec.minimum)
            slider.setValue(
                round((float(current) - spec.minimum) / span * _SLIDER_STEPS)
            )
            slider.valueChanged.connect(self._schedule)

            def read_float(slider=slider, spec=spec, span=span) -> float:
                return round(spec.minimum + span * slider.value() / _SLIDER_STEPS, 3)

            return slider, read_float
        if spec.kind == "int":
            spin = QSpinBox()
            spin.setRange(int(spec.minimum), int(spec.maximum))
            spin.setValue(int(current))
            spin.valueChanged.connect(self._schedule)
            return spin, spin.value
        combo = QComboBox()  # "choice"
        for choice in spec.choices:
            combo.addItem(choice, choice)
        combo.setCurrentIndex(max(0, combo.findData(str(current))))
        combo.currentIndexChanged.connect(self._schedule)
        return combo, lambda combo=combo: str(combo.currentData())

    # ── values ──────────────────────────────────────────────────────────
    def values(self) -> dict:
        return {key: reader() for key, reader in self._readers.items()}

    def control(self, key: str):
        """The widget editing `key` (tests drive controls through this)."""
        return self._controls.get(key)

    def flush(self) -> None:
        """Emit a pending change right away (tests, or before a rebuild)."""
        if self._debounce.isActive():
            self._debounce.stop()
            self._emit_now()

    def _schedule(self, *_args) -> None:
        self._debounce.start()  # restarts the wait on every new change

    def _emit_now(self) -> None:
        self.params_changed.emit(self.values())
