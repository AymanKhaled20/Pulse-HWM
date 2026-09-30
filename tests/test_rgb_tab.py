"""RGB tab: every visible control must save something and ask for a
re-plan, and the status/preview must reflect what the worker reports.
Offscreen Qt; a fake manager records re-plan requests."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pulse_hwm import app_settings  # noqa: E402
from pulse_hwm.db import Database  # noqa: E402
from pulse_hwm.rgb.assignment_store import entry_of  # noqa: E402
from pulse_hwm.rgb.effects.catalog import EffectCatalog  # noqa: E402
from pulse_hwm.rgb.model import RgbDevice  # noqa: E402
from pulse_hwm.ui.rgb_tab import MODE_IDS, RgbTab  # noqa: E402

DEVICES = [
    RgbDevice("openrgb:0", "AULA F75 <b>kbd</b>", "openrgb", leds=84),
    RgbDevice("openrgb:1", "ARGB STRIP", "openrgb", leds=10),
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeManager:
    def __init__(self) -> None:
        self.catalog = EffectCatalog()
        self.reconsiders = 0
        self.forced = 0

    def reconsider(self) -> bool:
        self.reconsiders += 1
        return True

    def force_reconsider(self) -> bool:
        self.forced += 1
        return True


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "rgb-tab.db")


@pytest.fixture()
def manager() -> FakeManager:
    return FakeManager()


@pytest.fixture()
def tab(app, db, manager):
    widget = RgbTab(db, manager=manager, brightness_bridge=lambda pct: None)
    widget.show_driver("openrgb", "OpenRGB", DEVICES)
    return widget


def _settings(db):
    return app_settings.load(db)


# ── mode buttons ────────────────────────────────────────────────────────
def test_mode_button_saves_mode_switches_page_and_replans(tab, db, manager):
    tab._mode_buttons["override"].click()
    assert _settings(db).rgb_mode == "override"
    assert tab._pages.currentIndex() == MODE_IDS.index("override")
    assert manager.reconsiders >= 1


def test_saved_mode_restored_on_load(app, db, manager):
    app_settings.save_field(db, "rgb_mode", "reactive")
    widget = RgbTab(db, manager=manager)
    assert widget._mode_buttons["reactive"].isChecked()
    assert widget._pages.currentIndex() == MODE_IDS.index("reactive")


# ── EFFECTS page ────────────────────────────────────────────────────────
def test_reactive_effects_not_offered_per_device(tab):
    combo = tab._device_rows["openrgb:0"].combo
    ids = [combo.itemData(i) for i in range(combo.count())]
    assert "reactive_temp" not in ids and "reactive_alert" not in ids
    assert "rainbow" in ids and "wave" in ids and "spectrum" in ids


def test_ticking_a_device_saves_its_effect_and_replans(tab, db, manager):
    row = tab._device_rows["openrgb:0"]
    row.checkbox.click()
    entry = entry_of(_settings(db).rgb_device_assignment, "openrgb", "openrgb:0")
    assert entry["effect"] == "static"
    assert "color" in entry["params"]  # saved with what the editor shows
    assert manager.reconsiders >= 1


def test_device_color_change_is_saved(tab, db, manager):
    row = tab._device_rows["openrgb:0"]
    row.checkbox.click()
    before = manager.reconsiders
    row.editor.control("color")._on_picked("#00FF00")  # as the picker would
    row.editor.flush()
    entry = entry_of(_settings(db).rgb_device_assignment, "openrgb", "openrgb:0")
    assert entry["params"]["color"] == "#00FF00"
    assert manager.reconsiders > before


def test_picking_an_effect_enables_the_device_and_keeps_its_color(tab, db):
    row = tab._device_rows["openrgb:1"]
    row.checkbox.click()
    row.editor.control("color")._on_picked("#0000FF")
    row.editor.flush()
    row.combo.setCurrentIndex(row.combo.findData("wave"))
    entry = entry_of(_settings(db).rgb_device_assignment, "openrgb", "openrgb:1")
    assert entry["effect"] == "wave"
    assert entry["params"]["color"] == "#0000FF"
    # the editor now shows WAVE's controls, including SPEED
    assert row.editor.control("speed") is not None


def test_unticking_removes_the_assignment(tab, db):
    row = tab._device_rows["openrgb:0"]
    row.checkbox.click()
    row.checkbox.click()
    assert entry_of(_settings(db).rgb_device_assignment, "openrgb", "openrgb:0") is None
    assert not row.editor.isEnabled()


# ── OVERRIDE page ───────────────────────────────────────────────────────
def test_override_effect_is_saved(tab, db):
    combo = tab._override_effect
    combo.setCurrentIndex(combo.findData("rainbow"))
    assert _settings(db).rgb_override_effect == "rainbow"
    # rainbow has SPEED but no COLOR → the editor only shows what it uses
    assert tab._override_editor.control("speed") is not None
    assert tab._override_editor.control("color") is None


def test_override_speed_is_saved_as_percent(tab, db):
    combo = tab._override_effect
    combo.setCurrentIndex(combo.findData("wave"))
    slider = tab._override_editor.control("speed")
    slider.setValue(slider.maximum())
    tab._override_editor.flush()
    assert _settings(db).rgb_override_speed == 100


# ── REACTIVE page ───────────────────────────────────────────────────────
def test_reactive_settings_are_saved(tab, db):
    tab._reactive_source.setCurrentIndex(tab._reactive_source.findData("gpu"))
    tab._reactive_low.setValue(30)
    tab._reactive_high.setValue(70)
    tab._reactive_hot._on_picked("#FF00FF")
    values = _settings(db)
    assert values.rgb_reactive_source == "gpu"
    assert (values.rgb_temp_low_c, values.rgb_temp_high_c) == (30, 70)
    assert values.rgb_temp_high_color == "#FF00FF"


def test_hot_stays_above_cool(tab, db):
    tab._reactive_high.setValue(50)
    tab._reactive_low.setValue(60)
    assert tab._reactive_high.value() == 61
    assert _settings(db).rgb_temp_high_c == 61


def test_reactive_readout_uses_live_sensor(tab):
    tab._mode_buttons["reactive"].click()
    tab.show_sensors({"temps": [{"label": "CPU Package", "temp": 85.0}]})
    # 85 °C is the default HOT point → the default hot color
    assert "85" in tab._reactive_now.text()
    assert tab._reactive_now_strip.colors()[0] == "#FF3B30"


# ── status line + preview ───────────────────────────────────────────────
def test_status_shows_live_and_paints_preview(tab):
    tab._mode_buttons["effects"].click()
    tab.show_status(
        {
            "fps": 29.7,
            "driven": 1,
            "ok": 1,
            "frames": {"openrgb:0": ["#00FF00"] * 84},
            "errors": {},
        }
    )
    assert tab._status_line.text().startswith("LIVE · 30 FPS · 1/1")
    assert tab._preview_rows["openrgb:0"].strip.colors() == ["#00FF00"] * 84
    assert tab._preview_rows["openrgb:1"].state.text() == "not driven by Pulse"


def test_status_shows_problem_then_recovers(tab):
    tab._mode_buttons["effects"].click()
    tab.show_status(
        {
            "fps": 30,
            "driven": 1,
            "ok": 0,
            "frames": {"openrgb:0": []},
            "errors": {"openrgb:0": "device never applied frames"},
        }
    )
    assert tab._status_line.text().startswith("PROBLEM")
    assert "device never applied frames" in tab._errors_label.text()
    tab.show_status(
        {
            "fps": 30,
            "driven": 1,
            "ok": 1,
            "frames": {"openrgb:0": ["#FFFFFF"]},
            "errors": {},
        }
    )
    assert tab._status_line.text().startswith("LIVE")
    assert tab._errors_label.text() == ""


def test_no_driver_is_reported(tab):
    tab.show_driver("", "", [])
    assert tab._status_line.text() == "NO RGB DRIVER"


def test_resend_forces_a_replan_and_says_so(tab, manager):
    tab._mode_buttons["effects"].click()
    tab._resend_btn.click()
    assert manager.forced == 1
    assert "RE-SENT TO 2 DEVICES" in tab._feedback.text()


def test_resend_in_off_mode_explains_instead(tab, manager):
    tab._mode_buttons["off"].click()
    tab._resend_btn.click()
    assert manager.forced == 0
    assert "OFF" in tab._feedback.text()


def test_brightness_slider_forwards_and_saves(app, db, manager):
    sent: list[int] = []
    widget = RgbTab(db, manager=manager, brightness_bridge=sent.append)
    widget._brightness.setValue(40)
    assert sent[-1] == 40
    widget._brightness_save.timeout.emit()  # skip the debounce wait
    assert _settings(db).rgb_brightness == 40


def test_color_button_opens_the_picker(tab):
    swatch = tab._reactive_cool
    swatch.click()
    assert swatch._popup is not None and swatch._popup.isVisible()
    swatch._popup.close()


# ── safety ──────────────────────────────────────────────────────────────
def test_device_names_are_shown_as_plain_text(tab):
    from PySide6.QtCore import Qt

    # a device name containing HTML must not be rendered as markup
    labels = [
        tab._preview_form.itemAt(i).widget()
        for i in range(tab._preview_form.count())
        if tab._preview_form.itemAt(i).widget() is not None
    ]
    names = [label for label in labels if "AULA" in label.text()]
    assert names and names[0].textFormat() == Qt.TextFormat.PlainText
