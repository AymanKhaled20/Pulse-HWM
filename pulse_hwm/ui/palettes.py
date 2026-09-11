"""Theme registry: 15 color palettes + 15 font themes.

A ColorTheme is the full surface palette; a FontTheme maps the UI's three
typographic roles (TITLE / DISPLAY / BODY) onto font families with sizes.

This module is pure data (no Qt imports) so it can be unit-tested without a
QApplication. `theme.py` turns the active pair into live module constants and
a rendered stylesheet; the THEMES tab lets the user pick, and
`theme_manager.py` applies + persists the choice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorTheme:
    """One full UI palette. The first entry (yellows on black) is the
    classic Pulse look and must stay the default."""

    id: str
    label: str
    bg: str
    panel: str
    panel_alt: str
    line: str
    primary: str
    highlight: str
    danger: str
    success: str
    text: str
    muted: str


COLOR_THEMES: tuple[ColorTheme, ...] = (
    ColorTheme(
        "amber",
        "AMBER (DEFAULT)",
        "#0A0A0A",
        "#141414",
        "#0F0F0F",
        "#2E2E2E",
        "#FFD400",
        "#FFE873",
        "#FF3B30",
        "#9BE800",
        "#E8E8E8",
        "#6A6A6A",
    ),
    ColorTheme(
        "matrix",
        "MATRIX",
        "#030703",
        "#081108",
        "#050B05",
        "#13401B",
        "#00FF41",
        "#7DFFAB",
        "#FF5545",
        "#00E6C3",
        "#C8FFD4",
        "#3C7A50",
    ),
    ColorTheme(
        "ice",
        "ICE",
        "#040A12",
        "#0A1524",
        "#071019",
        "#1C3A5E",
        "#4FD8FF",
        "#A7ECFF",
        "#FF6B5E",
        "#8AE68C",
        "#DFF2FF",
        "#5E7F99",
    ),
    ColorTheme(
        "crimson",
        "CRIMSON",
        "#0C0405",
        "#160A0C",
        "#0F0708",
        "#4A1418",
        "#FF4757",
        "#FF8A94",
        "#E61700",
        "#7CE577",
        "#FFE3E5",
        "#7E4A4E",
    ),
    ColorTheme(
        "synthwave",
        "SYNTHWAVE",
        "#0D0518",
        "#160B28",
        "#100821",
        "#34215E",
        "#FF2EC8",
        "#B96BFF",
        "#FF5E5B",
        "#00F5A0",
        "#F3E9FF",
        "#7A5CA8",
    ),
    ColorTheme(
        "gameboy",
        "GAME BOY",
        "#9BBC0F",
        "#8BAC0F",
        "#7B9C0F",
        "#306230",
        "#306230",
        "#415C41",
        "#B2451E",
        "#2E5433",
        "#0F380F",
        "#5A7A24",
    ),
    ColorTheme(
        "paper",
        "PAPER",
        "#D9D9D4",
        "#F2F2EE",
        "#EAEAE5",
        "#C6C4B8",
        "#D97706",
        "#F3B23C",
        "#DC2626",
        "#16A34A",
        "#1F2937",
        "#6B7280",
    ),
    ColorTheme(
        "cobalt",
        "COBALT",
        "#05070E",
        "#0C1122",
        "#080D1B",
        "#22335C",
        "#4D7CFF",
        "#8FB0FF",
        "#FF5C5C",
        "#3DDC97",
        "#E3E9FF",
        "#5D6C99",
    ),
    ColorTheme(
        "ember",
        "EMBER",
        "#0A0603",
        "#160D07",
        "#0F0905",
        "#523017",
        "#FF7B00",
        "#FFB25C",
        "#FF3B30",
        "#A3E635",
        "#FFEAD9",
        "#8A6A4E",
    ),
    ColorTheme(
        "toxic",
        "TOXIC",
        "#070A02",
        "#101704",
        "#0B1103",
        "#3A4A0E",
        "#C6FF00",
        "#E7FF5C",
        "#FF4242",
        "#00E5A0",
        "#F2FFD9",
        "#71802E",
    ),
    ColorTheme(
        "steel",
        "STEEL",
        "#0B0D0F",
        "#16191C",
        "#101316",
        "#32383E",
        "#E2E8F0",
        "#FFFFFF",
        "#F87171",
        "#4ADE80",
        "#E2E8F0",
        "#737C85",
    ),
    ColorTheme(
        "grape",
        "GRAPE",
        "#0B0512",
        "#170B26",
        "#110820",
        "#3B2166",
        "#A78BFA",
        "#D6C6FF",
        "#FB7185",
        "#34D399",
        "#F1EAFE",
        "#6F5B94",
    ),
    ColorTheme(
        "lagoon",
        "LAGOON",
        "#02100F",
        "#07201D",
        "#041614",
        "#14514A",
        "#2DD4BF",
        "#7CF7E4",
        "#FF6459",
        "#84CC16",
        "#D9FFF8",
        "#4E8578",
    ),
    ColorTheme(
        "sakura",
        "SAKURA",
        "#140A0E",
        "#24121A",
        "#190D13",
        "#6E2B44",
        "#FF7FA5",
        "#FFC2D2",
        "#FF4E3E",
        "#86EFAC",
        "#FFE8EF",
        "#8F6376",
    ),
    ColorTheme(
        "hunter",
        "HUNTER",
        "#050D08",
        "#0B1C12",
        "#08150D",
        "#1C4030",
        "#FFC53D",
        "#FFE08A",
        "#FF7849",
        "#8AE68C",
        "#E7F5EC",
        "#4E7A5E",
    ),
)

COLOR_THEME_IDS: tuple[str, ...] = tuple(t.id for t in COLOR_THEMES)
DEFAULT_COLOR_THEME = "amber"


@dataclass(frozen=True)
class FontTheme:
    """Map the UI's three type roles onto families.

    title   — window/section headlines (was 'Press Start 2P')
    display — labels, tabs, buttons, table headers (was 'Silkscreen')
    body    — numbers, tables, inputs, menus (was 'VT323')

    Pixel fonts are designed on small grids and need their own px sizes;
    normal desktop fonts need bigger sizes to read the same way, so every
    theme carries its own size set.
    """

    id: str
    label: str
    title: str
    title_px: int
    display: str
    display_px: int
    body: str
    body_px: int
    header_px: int


FONT_THEMES: tuple[FontTheme, ...] = (
    FontTheme(
        "classic",
        "CLASSIC PIXEL",
        "Press Start 2P",
        16,
        "Silkscreen",
        10,
        "VT323",
        18,
        9,
    ),
    FontTheme(
        "terminal",
        "TERMINAL (ALL VT323)",
        "VT323",
        22,
        "VT323",
        14,
        "VT323",
        18,
        12,
    ),
    FontTheme(
        "pixel-hd",
        "PIXEL HD",
        "Press Start 2P",
        13,
        "Pixelify Sans",
        12,
        "JetBrains Mono",
        13,
        10,
    ),
    FontTheme(
        "dot-matrix",
        "DOT MATRIX",
        "DotGothic16",
        15,
        "DotGothic16",
        12,
        "JetBrains Mono",
        13,
        10,
    ),
    FontTheme(
        "jersey",
        "JERSEY SCOREBOARD",
        "Jersey 10",
        20,
        "Jersey 10",
        13,
        "IBM Plex Mono",
        14,
        11,
    ),
    FontTheme(
        "tiny",
        "TINY5",
        "Tiny5",
        20,
        "Tiny5",
        13,
        "DM Mono",
        13,
        11,
    ),
    FontTheme(
        "elastic",
        "HANDJET",
        "Handjet",
        20,
        "Handjet",
        13,
        "VT323",
        18,
        11,
    ),
    FontTheme(
        "orbit-tech",
        "ORBIT TECH",
        "Orbitron",
        13,
        "Chakra Petch",
        12,
        "Share Tech Mono",
        15,
        10,
    ),
    FontTheme(
        "audiocode",
        "AUDIO / FIRA",
        "Audiowide",
        13,
        "IBM Plex Mono",
        12,
        "Fira Code",
        13,
        10,
    ),
    FontTheme(
        "codystar",
        "DYING STAR",
        "Codystar",
        20,
        "Share Tech Mono",
        12,
        "Share Tech Mono",
        14,
        10,
    ),
    FontTheme(
        "space-ano",
        "SPACE TYPEWRITER",
        "Space Mono",
        15,
        "Anonymous Pro",
        12,
        "Space Mono",
        13,
        10,
    ),
    FontTheme(
        "press-ano",
        "PRESS + TYPEWRITER",
        "Press Start 2P",
        14,
        "Anonymous Pro",
        12,
        "Anonymous Pro",
        14,
        10,
    ),
    FontTheme(
        "chakra",
        "CHAKRA TECH",
        "Audiowide",
        13,
        "Chakra Petch",
        12,
        "IBM Plex Mono",
        14,
        10,
    ),
    FontTheme(
        "mono-glass",
        "MONO GLASS",
        "Orbitron",
        13,
        "Fira Code",
        11,
        "DM Mono",
        13,
        10,
    ),
    FontTheme(
        "retro-doc",
        "RETRO OFFICE",
        "Press Start 2P",
        13,
        "Anonymous Pro",
        11,
        "Fira Code",
        13,
        9,
    ),
)

FONT_THEME_IDS: tuple[str, ...] = tuple(t.id for t in FONT_THEMES)
DEFAULT_FONT_THEME = "classic"


def color_theme(theme_id: str) -> ColorTheme:
    for t in COLOR_THEMES:
        if t.id == theme_id:
            return t
    return COLOR_THEMES[0]  # unknown/legacy id falls back to the default


def font_theme(theme_id: str) -> FontTheme:
    for t in FONT_THEMES:
        if t.id == theme_id:
            return t
    return FONT_THEMES[0]


# Every color theme must expose the same fields; the QSS template and the
# custom-paint widgets depend on this set existing on all of them.
COLOR_FIELDS = (
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
