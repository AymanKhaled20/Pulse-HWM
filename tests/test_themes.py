from __future__ import annotations

import re

import pytest

from pulse_hwm import app_settings
from pulse_hwm.db import Database
from pulse_hwm.ui.palettes import (
    COLOR_THEME_IDS,
    COLOR_THEMES,
    DEFAULT_COLOR_THEME,
    DEFAULT_FONT_THEME,
    FONT_THEME_IDS,
    FONT_THEMES,
    color_theme,
    font_theme,
)
from pulse_hwm.ui.theme import render_qss

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
LEFTOVER_TOKEN_RE = re.compile(r"\$[A-Z_0-9]+")


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "themes.db")


def test_registries_have_fifteen_entries():
    assert len(COLOR_THEMES) == 15
    assert len(FONT_THEMES) == 15


def test_theme_ids_are_unique():
    assert len(set(COLOR_THEME_IDS)) == 15
    assert len(set(FONT_THEME_IDS)) == 15


def test_default_themes_come_first():
    """The classic yellow/black look must stay the shipped default."""
    assert COLOR_THEME_IDS[0] == DEFAULT_COLOR_THEME == "amber"
    assert FONT_THEME_IDS[0] == DEFAULT_FONT_THEME == "classic"


def test_color_palettes_are_complete_and_valid_hex():
    fields = (
        "bg",
        "panel",
        "panel_alt",
        "line",
        "primary",
        "highlight",
        "danger",
        "success",
        "text",
        "muted",
    )
    for t in COLOR_THEMES:
        for field in fields:
            value = getattr(t, field)
            assert HEX_RE.match(value), f"{t.id}.{field} = {value!r}"


def test_unknown_theme_ids_fall_back_to_default():
    # a stale DB key must never crash the boot sequence
    assert color_theme("does-not-exist").id == "amber"
    assert font_theme("does-not-exist").id == "classic"


def test_render_qss_leaves_no_unrendered_tokens():
    for ct in (COLOR_THEMES[0], color_theme("paper")):
        for ft in (FONT_THEMES[0], font_theme("orbit-tech")):
            out = render_qss(ct, ft)
            assert not LEFTOVER_TOKEN_RE.search(out), "unrendered $TOKEN in QSS"
            assert ct.primary in out
            assert ft.body in out


def test_theme_settings_roundtrip_survives_reload(db):
    app_settings.save_field(db, "theme_color", "matrix")
    app_settings.save_field(db, "theme_font", "orbit-tech")
    values = app_settings.load(db)
    assert values.theme_color == "matrix"
    assert values.theme_font == "orbit-tech"


def test_theme_settings_defaults(db):
    values = app_settings.load(db)
    assert values.theme_color == "amber"
    assert values.theme_font == "classic"
