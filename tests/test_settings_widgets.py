"""M6 settings widgets tests (offscreen Qt, temp SQLite)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QCheckBox, QLabel, QSpinBox, QWidget

from pulse_hwm.db import Database
from pulse_hwm.ui.widgets.settings_widgets import (
    CollapsiblePanel,
    FormRow,
    SettingsRail,
)


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def db(tmp_path: Path):
    return Database(tmp_path / "widgets-test.db")


class TestFormRow:
    def test_row_shows_label_and_control(self, app, db):
        spinner = QSpinBox()
        row = FormRow("HARDWARE INTERVAL", spinner)
        assert row.control is spinner

    def test_help_present_when_text(self, app, db):
        row = FormRow("X", QCheckBox(), "blurb")
        assert isinstance(row.help(), QLabel)

    def test_help_absent_without_text(self, app, db):
        row = FormRow("X", QCheckBox())
        assert row.help() is None


class TestCollapsiblePanel:
    def test_starts_expanded_by_default(self, app, db):
        panel = CollapsiblePanel("ALERTS", db)
        assert panel._collapsed is False

    def test_toggle_flips_state_and_persists(self, app, db):
        panel = CollapsiblePanel("ALERTS", db)
        panel.toggle()
        assert panel._collapsed is True
        state = db.get_setting("settings_collapsed::alerts", "")
        assert state == "1"

    def test_state_restored_on_rebuild(self, app, db):
        panel = CollapsiblePanel("SOME SECTION", db)
        panel.toggle()  # collapsed + persisted
        second = CollapsiblePanel("SOME SECTION", db)
        assert second._collapsed is True


class TestSettingsRail:
    def test_select_switches_stack(self, app, db):
        rail = SettingsRail()
        page_a, page_b = QWidget(), QWidget()
        rail.add_section("PAGE ONE", page_a)
        rail.add_section("PAGE TWO", page_b)
        rail.add_stretch()
        assert rail.active_section() == 0
        assert rail._stack.currentWidget() is page_a
        rail.select(1)
        assert rail._stack.currentWidget() is page_b

    def test_buttons_follow_active_page(self, app, db):
        rail = SettingsRail()
        rail.add_section("ONE", QWidget())
        rail.add_section("TWO", QWidget())
        rail.select(1)
        assert rail._buttons[1].isChecked() is True
        assert rail._buttons[0].isChecked() is False
