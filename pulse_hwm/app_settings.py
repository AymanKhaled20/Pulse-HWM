from __future__ import annotations

from dataclasses import dataclass

from pulse_hwm import config
from pulse_hwm.db import Database
from pulse_hwm.util import str_to_bool


@dataclass
class AppSettings:
    hardware_interval_ms: int = 1000
    website_interval_s: int = 30
    website_timeout_s: float = 10.0
    ssl_warn_days: int = 14
    retention_days: int = 30
    sound_enabled: bool = True
    desktop_enabled: bool = True
    webhooks_enabled: bool = True
    # ── processes / resources ──────────────────────────────────────────
    process_interval_s: int = 3
    process_max_rows: int = 400
    limit_resources: bool = False  # low priority + periodic working-set trim

    # ── updates (v1.2.0) ────────────────────────────────────────────────
    # auto-update checks; the CHECK/highest-seen/dismiss state keys
    # (update_*) are device-local internals — deliberately NOT here and
    # NOT syncable, accessed only via db.set_setting/get_setting.
    update_check_enabled: bool = True

    # ── theme ─────────────────────────────────────────────────────────
    theme_color: str = "amber"  # id from ui/palettes.py COLOR_THEMES
    theme_font: str = "classic"  # id from ui/palettes.py FONT_THEMES
    font_size: int = 18  # UI base size in px; THEMES tab dropdown (14–18)

    # ── RGB control (v1.3.0) ─────────────────────────────────────────────
    # ALL rgb_* keys are DEVICE-LOCAL (never in SYNCABLE_KEYS): plugin ids
    # and device names differ per machine, so syncing them would push
    # meaningless keys onto other devices.
    # rgb_mode is the single source of truth for who drives the hardware:
    #   off      — Pulse never touches devices (vendor software keeps control)
    #   effects  — run each device's assigned effect
    #   reactive — link devices to hardware state (temp gradient, alerts)
    #   override — one forced color/effect for everything, continuously
    #              re-asserted so vendor software can't reclaim devices
    rgb_mode: str = "off"
    rgb_engine_fps: int = 30  # render rate (5–60); continuous rendering costs CPU
    rgb_reassert_s: int = 2  # re-apply cadence (vendor services reclaim devices)
    rgb_brightness: int = 100
    rgb_override_effect: str = "static"
    rgb_override_color: str = "#FFD400"  # "#RRGGBB"; validated on load
    rgb_reactive_source: str = "cpu"  # cpu | gpu | mem | max_temp
    rgb_temp_low_c: int = 40  # gradient endpoints (cool color at low_c)
    rgb_temp_high_c: int = 85  # …hot color at high_c
    rgb_temp_low_color: str = "#00FF41"
    rgb_temp_high_color: str = "#FF3B30"
    rgb_alert_color: str = "#FF3B30"
    rgb_alert_hold_ms: int = 4000  # how long an alert flash overrides reactive
    rgb_device_assignment: str = "{}"  # JSON: {device_key: {effect, params…}}
    rgb_user_effects: str = "[]"  # JSON: imported declarative effect defs
    rgb_allow_external_plugins: bool = False  # user drop-in drivers (RCE risk)
    rgb_allow_effect_urls: bool = False  # URL effect import (network content)


_INT_KEYS = {
    "hardware_interval_ms",
    "website_interval_s",
    "ssl_warn_days",
    "retention_days",
    "process_interval_s",
    "process_max_rows",
    "font_size",
    "rgb_engine_fps",
    "rgb_reassert_s",
    "rgb_brightness",
    "rgb_temp_low_c",
    "rgb_temp_high_c",
    "rgb_alert_hold_ms",
}
_FLOAT_KEYS = {"website_timeout_s"}
_BOOL_KEYS = {
    "sound_enabled",
    "desktop_enabled",
    "webhooks_enabled",
    "limit_resources",
    "update_check_enabled",
    "rgb_allow_external_plugins",
    "rgb_allow_effect_urls",
}
_STR_KEYS = {
    "theme_color",
    "theme_font",
    "rgb_mode",
    "rgb_override_effect",
    "rgb_override_color",
    "rgb_reactive_source",
    "rgb_temp_low_color",
    "rgb_temp_high_color",
    "rgb_alert_color",
    "rgb_device_assignment",
    "rgb_user_effects",
}
_ALL_KEYS = _INT_KEYS | _FLOAT_KEYS | _BOOL_KEYS | _STR_KEYS

LIMITS = {
    "hardware_interval_ms": (250, 10_000),
    "website_interval_s": (5, 3_600),
    "website_timeout_s": (1.0, 120.0),
    "ssl_warn_days": (1, 90),
    "retention_days": (1, 365),
    "process_interval_s": (1, 60),
    "process_max_rows": (50, 2_000),
    "font_size": (14, 18),
    "rgb_engine_fps": (5, 60),
    "rgb_reassert_s": (1, 30),
    "rgb_brightness": (0, 100),
    "rgb_temp_low_c": (0, 100),
    "rgb_temp_high_c": (0, 150),
    "rgb_alert_hold_ms": (500, 30_000),
}

# rgb_mode values, in escalating order of control. The engine resolves the
# active behavior from this enum — never from independent booleans — so there
# is exactly one source of truth and no ambiguous "both are on" state.
RGB_MODES = ("off", "effects", "reactive", "override")
RGB_REACTIVE_SOURCES = ("cpu", "gpu", "mem", "max_temp")

# Cloud sync classification (per plan): portable preferences sync;
# hardware/performance keys are per-device and NEVER leave the machine.
SYNCABLE_KEYS = frozenset(
    {
        "theme_color",
        "theme_font",
        "font_size",
        "sound_enabled",
        "desktop_enabled",
        "webhooks_enabled",
        "ssl_warn_days",
        "website_interval_s",
        "website_timeout_s",
    }
)


def load(db: Database) -> AppSettings:
    env = config.env()
    values = AppSettings(hardware_interval_ms=1000)

    defaults = {
        "sound_enabled": str(env.alert_sound_enabled).lower(),
    }

    def read(key: str, cast: type, default) -> object:
        raw = db.get_setting(key, defaults.get(key, ""))
        if raw == "":
            return default
        try:
            return cast(raw)
        except (TypeError, ValueError):
            return default

    values.hardware_interval_ms = int(
        read("hardware_interval_ms", int, values.hardware_interval_ms)
    )
    values.website_interval_s = int(
        read("website_interval_s", int, values.website_interval_s)
    )
    values.website_timeout_s = float(
        read("website_timeout_s", float, values.website_timeout_s)
    )
    values.ssl_warn_days = int(read("ssl_warn_days", int, values.ssl_warn_days))
    values.retention_days = int(read("retention_days", int, values.retention_days))
    values.process_interval_s = int(
        read("process_interval_s", int, values.process_interval_s)
    )
    values.process_max_rows = int(
        read("process_max_rows", int, values.process_max_rows)
    )
    values.sound_enabled = bool(
        read("sound_enabled", str_to_bool, values.sound_enabled)
        in (True, "true", "True", 1)
    )
    values.desktop_enabled = bool(
        read("desktop_enabled", str_to_bool, values.desktop_enabled)
        in (True, "true", "True", 1)
    )
    values.webhooks_enabled = bool(
        read("webhooks_enabled", str_to_bool, values.webhooks_enabled)
        in (True, "true", "True", 1)
    )
    values.limit_resources = bool(
        read("limit_resources", str_to_bool, values.limit_resources)
        in (True, "true", "True", 1)
    )
    values.update_check_enabled = bool(
        read("update_check_enabled", str_to_bool, values.update_check_enabled)
        in (True, "true", "True", 1)
    )
    values.theme_color = str(read("theme_color", str, values.theme_color)) or "amber"
    values.theme_font = str(read("theme_font", str, values.theme_font)) or "classic"
    values.font_size = int(read("font_size", int, values.font_size))
    lo, hi = LIMITS["font_size"]
    values.font_size = max(lo, min(hi, values.font_size))

    # ── RGB keys ─────────────────────────────────────────────────────
    # Enum-valued strings fall back to the default when the stored value is
    # unknown (e.g. after a downgrade), instead of propagating garbage into
    # the engine's mode resolution.
    values.rgb_mode = _read_enum(read, "rgb_mode", RGB_MODES, values.rgb_mode)
    values.rgb_engine_fps = _read_limited_int(
        read, "rgb_engine_fps", values.rgb_engine_fps
    )
    values.rgb_reassert_s = _read_limited_int(
        read, "rgb_reassert_s", values.rgb_reassert_s
    )
    values.rgb_brightness = _read_limited_int(
        read, "rgb_brightness", values.rgb_brightness
    )
    values.rgb_override_effect = str(
        read("rgb_override_effect", str, values.rgb_override_effect)
    )
    values.rgb_override_color = _read_hex(
        read, "rgb_override_color", values.rgb_override_color
    )
    values.rgb_reactive_source = _read_enum(
        read, "rgb_reactive_source", RGB_REACTIVE_SOURCES, values.rgb_reactive_source
    )
    values.rgb_temp_low_c = _read_limited_int(
        read, "rgb_temp_low_c", values.rgb_temp_low_c
    )
    values.rgb_temp_high_c = _read_limited_int(
        read, "rgb_temp_high_c", values.rgb_temp_high_c
    )
    values.rgb_temp_low_color = _read_hex(
        read, "rgb_temp_low_color", values.rgb_temp_low_color
    )
    values.rgb_temp_high_color = _read_hex(
        read, "rgb_temp_high_color", values.rgb_temp_high_color
    )
    values.rgb_alert_color = _read_hex(read, "rgb_alert_color", values.rgb_alert_color)
    values.rgb_alert_hold_ms = _read_limited_int(
        read, "rgb_alert_hold_ms", values.rgb_alert_hold_ms
    )
    # JSON blobs are validated where they're parsed (engine/effects loader);
    # here we only guarantee a string that parses as the right JSON type, so
    # a corrupted row degrades to the empty structure instead of crashing
    # every later load.
    values.rgb_device_assignment = _read_json_object(
        read, "rgb_device_assignment", values.rgb_device_assignment
    )
    values.rgb_user_effects = _read_json_list(
        read, "rgb_user_effects", values.rgb_user_effects
    )
    values.rgb_allow_external_plugins = bool(
        read(
            "rgb_allow_external_plugins", str_to_bool, values.rgb_allow_external_plugins
        )
        in (True, "true", "True", 1)
    )
    values.rgb_allow_effect_urls = bool(
        read("rgb_allow_effect_urls", str_to_bool, values.rgb_allow_effect_urls)
        in (True, "true", "True", 1)
    )
    return values


def save(db: Database, values: AppSettings) -> None:
    for key in _ALL_KEYS:
        raw = getattr(values, key)
        db.set_setting(key, str(int(raw)) if isinstance(raw, bool) else str(raw))


def save_field(db: Database, key: str, value) -> None:
    """Persist ONE setting immediately (booleans become "1"/"0").

    Used by toggles that must survive a restart all by themselves — the
    LIMIT PULSE RESOURCES checkbox should stick the instant it is clicked,
    without the user having to find the APPLY button in another panel.
    Raises ValueError on an unknown key so a typo can't silently no-op.
    """
    if key not in _ALL_KEYS:
        raise ValueError(f"unknown setting key: {key}")
    db.set_setting(key, str(int(value)) if isinstance(value, bool) else str(value))


def clamp(key: str, value: float) -> float:
    low, high = LIMITS.get(key, (0, 1e9))
    return max(low, min(high, value))


# ── RGB load-time validation helpers ─────────────────────────────────────
# Kept module-level and pure so tests can exercise them without a Database.


def _read_enum(read, key: str, allowed: tuple[str, ...], default: str) -> str:
    value = str(read(key, str, default))
    return value if value in allowed else default


def _read_limited_int(read, key: str, default: int) -> int:
    value = int(read(key, int, default))
    low, high = LIMITS.get(key, (0, 1_000_000))
    return max(low, min(high, value))


def _valid_hex(value: str) -> bool:
    text = (value or "").strip().lstrip("#")
    if len(text) != 6:
        return False
    try:
        int(text, 16)
        return True
    except ValueError:
        return False


def _read_hex(read, key: str, default: str) -> str:
    value = str(read(key, str, default))
    return value if _valid_hex(value) else default


def _read_json_object(read, key: str, default: str) -> str:
    import json

    try:
        parsed = json.loads(str(read(key, str, default)) or "{}")
        return str(parsed) if isinstance(parsed, dict) else default
    except ValueError:
        return default


def _read_json_list(read, key: str, default: str) -> str:
    import json

    try:
        parsed = json.loads(str(read(key, str, default)) or "[]")
        return str(parsed) if isinstance(parsed, list) else default
    except ValueError:
        return default
