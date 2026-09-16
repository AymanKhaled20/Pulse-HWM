"""Core RGB data model — Qt-free, dependency-free.

Every later phase (effects, drivers, engine, UI) speaks this vocabulary, so
these dataclasses are a frozen contract: fields may be added with defaults,
but existing field names/types never change without a dedicated contract
phase.

Design notes:
  * RgbColor is stored as plain ints (0-255), not a Qt QColor and not a
    "#hex" string — conversions happen at the edges (settings load stores
    hex strings; drivers and effects exchange RgbColor).
  * RgbDevice.leds == 0 means "LED count unknown"; drivers that only support
    whole-device color report a single synthetic LED so the frame contract
    (one RgbColor per LED) stays uniform everywhere.
  * Frozen dataclasses throughout: frames and devices are passed across
    threads (worker → UI), and immutability removes a whole class of
    threading bugs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The canonical effect mode ids. Built-in effects register these; the
# declarative JSON format references them. Kept here (not in effects/) so
# the model has no import cycle with the effect package.
MODE_STATIC = "static"
MODE_BREATHE = "breathe"
MODE_RAINBOW = "rainbow"
MODE_WAVE = "wave"
MODE_SPECTRUM = "spectrum"
MODE_STROBE = "strobe"
MODE_OFF = "off"


def _clamp_byte(value: int) -> int:
    return max(0, min(255, int(value)))


@dataclass(frozen=True)
class RgbColor:
    """One 24-bit color. r/g/b are always clamped 0-255."""

    r: int = 0
    g: int = 0
    b: int = 0

    def __post_init__(self) -> None:
        # frozen dataclass: mutate via object.__setattr__ in post_init only
        object.__setattr__(self, "r", _clamp_byte(self.r))
        object.__setattr__(self, "g", _clamp_byte(self.g))
        object.__setattr__(self, "b", _clamp_byte(self.b))

    @classmethod
    def from_hex(cls, value: str) -> "RgbColor":
        """Parse '#RRGGBB' (leading '#' optional). Falls back to black on
        garbage — settings values come from user-editable storage, and a
        malformed string must degrade, not raise."""
        text = (value or "").strip().lstrip("#")
        if len(text) != 6:
            return cls(0, 0, 0)
        try:
            return cls(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        except ValueError:
            return cls(0, 0, 0)

    def to_hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    def scaled(self, factor: float) -> "RgbColor":
        """Brightness-scaled copy (factor 0.0-1.0)."""
        factor = max(0.0, min(1.0, float(factor)))
        return RgbColor(
            int(self.r * factor), int(self.g * factor), int(self.b * factor)
        )


@dataclass(frozen=True)
class LedLayout:
    """Spatial map of a device's LEDs.

    positions are normalized 0..1 (x, y) per LED, so effects can be written
    against geometry without knowing pixel densities. key_names (when not
    None) indexes 1:1 with positions and names each key; matrix gives
    (rows, cols) for grid-style effects on keyboards/pads.
    """

    led_count: int
    positions: tuple[tuple[float, float], ...] = ()
    key_names: tuple[str, ...] | None = None
    matrix: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.key_names is not None and len(self.key_names) != self.led_count:
            raise ValueError("key_names length must equal led_count")
        if self.positions and len(self.positions) != self.led_count:
            raise ValueError("positions length must equal led_count")


@dataclass(frozen=True)
class RgbDevice:
    """One controllable device as reported by a driver."""

    device_id: str
    name: str
    driver_id: str
    leds: int = 1  # 0/1 = whole-device control only
    layout: LedLayout | None = None
    modes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RgbEffect:
    """A requested effect instance (settings/UI → engine).

    mode is one of the MODE_* ids; params carries effect-specific values
    (speed, direction, …). color is the primary color for color-taking
    modes. Kept effect-agnostic so the declarative JSON layer can produce
    the same object.
    """

    mode: str = MODE_STATIC
    color: RgbColor = field(default_factory=lambda: RgbColor(255, 212, 0))
    brightness: int = 100  # 0-100
    speed: int = 50  # 0-100
    params: dict[str, float | int | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "brightness", max(0, min(100, int(self.brightness))))
        object.__setattr__(self, "speed", max(0, min(100, int(self.speed))))


def solid_frame(device: RgbDevice, color: RgbColor) -> list[RgbColor]:
    """A full frame of one color — the bridge between whole-device drivers
    and the per-LED frame contract."""
    count = max(1, device.leds)
    return [color] * count
