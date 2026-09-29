from __future__ import annotations

from dataclasses import dataclass, replace

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
    rgb_override_speed: int = 50  # 0-100 → effect-relative speed
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

    # ── OpenRGB backend (v1.4.0) ─────────────────────────────────────────
    # ALL RGB devices (AULA keyboard included) are served by a bundled
    # headless OpenRGB fork; see docs/RGB.md. enabled=False leaves hardware
    # untouched entirely.
    rgb_openrgb_enabled: bool = True
    rgb_openrgb_port: int = 6742  # SDK server port (loopback only)
    rgb_openrgb_path: str = ""  # optional OpenRGB.exe override ("" = bundled)


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
    "rgb_override_speed",
    "rgb_temp_low_c",
    "rgb_temp_high_c",
    "rgb_alert_hold_ms",
    "rgb_openrgb_port",
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
    "rgb_openrgb_enabled",
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
    "rgb_openrgb_path",
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
    "rgb_override_speed": (0, 100),
    "rgb_temp_low_c": (0, 100),
    "rgb_temp_high_c": (0, 150),
    "rgb_alert_hold_ms": (500, 30_000),
    "rgb_openrgb_port": (1024, 65535),
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


# String keys whose value must be one of a fixed set; an unknown stored value
# (e.g. written by a newer version, then downgraded) falls back to the default
# instead of propagating garbage into the engine's mode resolution.
_ENUM_KEYS: dict[str, tuple[str, ...]] = {
    "rgb_mode": RGB_MODES,
    "rgb_reactive_source": RGB_REACTIVE_SOURCES,
}
# "#RRGGBB" color keys; anything that isn't 6 hex digits falls back.
_HEX_KEYS = {
    "rgb_override_color",
    "rgb_temp_low_color",
    "rgb_temp_high_color",
    "rgb_alert_color",
}
# JSON blobs: here we only guarantee a string that parses as the right JSON
# type (full validation happens where they're parsed), so a corrupted row
# degrades to the empty structure instead of crashing every later load.
_JSON_OBJECT_KEYS = {"rgb_device_assignment"}
_JSON_LIST_KEYS = {"rgb_user_effects"}


def load(db: Database) -> AppSettings:
    """Read every setting from the DB into an AppSettings.

    Cached: the parsed result is kept on the Database object together with
    db.settings_version, which every settings write bumps. So repeat calls
    (the RGB planner, scheduler jobs, tabs) cost nothing until something
    actually changes. Callers get their own copy, so mutating the returned
    object (e.g. the Settings form building a save) can't corrupt the cache.
    """
    version = db.settings_version  # read BEFORE loading (see note below)
    cached = getattr(db, "_app_settings_cache", None)
    if cached is not None and cached[0] == version:
        return replace(cached[1])

    values = _load_uncached(db)
    # if a write lands while we were loading, it bumped the version past
    # the one we stored, so the next load() simply re-reads — never stale
    db._app_settings_cache = (version, values)
    return replace(values)


def _load_uncached(db: Database) -> AppSettings:
    stored = db.get_all_settings()  # ONE query instead of one per key
    values = AppSettings()
    # the only default that comes from .env rather than the dataclass
    env_defaults = {"sound_enabled": str(config.env().alert_sound_enabled).lower()}

    for key in _ALL_KEYS:
        default = getattr(values, key)
        raw = stored.get(key, env_defaults.get(key, ""))
        if raw == "":
            continue  # never set → keep the dataclass default
        setattr(values, key, _parse_value(key, raw, default))
    return values


def _parse_value(key: str, raw: str, default):
    """Turn one stored string into a typed, validated value. Anything that
    doesn't parse or validate falls back to `default` — settings rows are
    user-reachable storage and must never crash the app."""
    if key in _BOOL_KEYS:
        return str_to_bool(raw, default)
    if key in _INT_KEYS:
        try:
            number = int(raw)
        except (TypeError, ValueError):
            return default
        return int(clamp(key, number)) if key in LIMITS else number
    if key in _FLOAT_KEYS:
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return default
        return float(clamp(key, number)) if key in LIMITS else number
    # everything below is a string key
    if key in _ENUM_KEYS:
        return raw if raw in _ENUM_KEYS[key] else default
    if key in _HEX_KEYS:
        return raw if _valid_hex(raw) else default
    if key in _JSON_OBJECT_KEYS:
        return _normalize_json(raw, dict, default)
    if key in _JSON_LIST_KEYS:
        return _normalize_json(raw, list, default)
    return raw


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


# ── load-time validation helpers ─────────────────────────────────────────
# Kept module-level and pure so tests can exercise them without a Database.


def _valid_hex(value: str) -> bool:
    text = (value or "").strip().lstrip("#")
    if len(text) != 6:
        return False
    try:
        int(text, 16)
        return True
    except ValueError:
        return False


def _normalize_json(raw: str, expected_type: type, default: str) -> str:
    import json

    try:
        parsed = json.loads(raw)
    except ValueError:
        return default
    # re-serialize with json.dumps: str(dict) produces repr with single
    # quotes, which is NOT valid JSON for a later json.loads
    return json.dumps(parsed) if isinstance(parsed, expected_type) else default
