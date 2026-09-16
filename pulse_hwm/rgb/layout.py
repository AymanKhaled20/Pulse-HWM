"""Device layout helpers — key grids and normalized geometry.

The LedLayout dataclass lives in model.py (frozen contract); this module
holds the *builders* that generate layouts, so drivers construct maps from
compact descriptions (rows × cols, key names) instead of hand-writing
hundreds of position tuples.

Positions are normalized 0..1 with (0,0) at the TOP-LEFT: y grows downward,
matching Qt paint coordinates and making effects like vertical gradients
read the same way they'll appear.
"""

from __future__ import annotations

from pulse_hwm.rgb.model import LedLayout

# AULA F75 (65%): 5 physical rows. Names follow the printed legends; the
# exact per-key HID index mapping lives in the Aula driver (phase 2) — this
# map is the spatial truth the effects engine renders against.
AULA_F75_ROWS: tuple[tuple[str, ...], ...] = (
    ("ESC", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=", "BACKSPACE"),
    ("TAB", "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "[", "]", "\\"),
    ("CAPS", "A", "S", "D", "F", "G", "H", "J", "K", "L", ";", "'", "ENTER"),
    ("SHIFT", "Z", "X", "C", "V", "B", "N", "M", ",", ".", "/", "UP", "SHIFT_R"),
    ("CTRL", "WIN", "ALT", "SPACE", "ALT_R", "FN", "CTRL_R", "LEFT", "DOWN", "RIGHT"),
)


def _weights(names: tuple[str, ...]) -> tuple[float, ...]:
    """Relative horizontal weight per key (SPACE/BACKSPACE are wider)."""
    wide = {
        "BACKSPACE": 2.0,
        "SPACE": 6.0,
        "SHIFT": 2.2,
        "SHIFT_R": 2.2,
        "ENTER": 2.2,
        "TAB": 1.5,
        "CAPS": 1.8,
        "CTRL": 1.5,
        "CTRL_R": 1.5,
    }
    return tuple(wide.get(name, 1.0) for name in names)


def keyboard_layout(rows: tuple[tuple[str, ...], ...]) -> LedLayout:
    """Build a normalized key grid: centers computed from per-row weights so
    wide keys (SPACE) occupy proportionally more x-range."""
    positions: list[tuple[float, float]] = []
    names: list[str] = []
    n_rows = len(rows)
    for r, row_names in enumerate(rows):
        weights = _weights(row_names)
        total = sum(weights)
        # each row keeps its own [0..1] span; y is row-centered
        x = 0.0
        y = (r + 0.5) / n_rows
        for name, w in zip(row_names, weights):
            cx = x + w / (2.0 * total)
            positions.append((cx, y))
            names.append(name)
            x += w / total
    return LedLayout(
        led_count=len(positions),
        positions=tuple(positions),
        key_names=tuple(names),
        matrix=(n_rows, max(len(r) for r in rows)),
    )


def aula_f75_layout() -> LedLayout:
    return keyboard_layout(AULA_F75_ROWS)


def grid_layout(rows: int, cols: int) -> LedLayout:
    """Uniform grid (fans, pads, LED matrices): cell centers."""
    positions: list[tuple[float, float]] = []
    for r in range(rows):
        for c in range(cols):
            positions.append(((c + 0.5) / cols, (r + 0.5) / rows))
    return LedLayout(
        led_count=len(positions), positions=tuple(positions), matrix=(rows, cols)
    )


def strip_layout(led_count: int, vertical: bool = False) -> LedLayout:
    """Linear strip (case LEDs, light bars) as a 1×N or N×1 grid."""
    if vertical:
        return grid_layout(led_count, 1)
    return grid_layout(1, led_count)
