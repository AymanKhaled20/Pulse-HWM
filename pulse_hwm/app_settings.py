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


_INT_KEYS = {
    "hardware_interval_ms",
    "website_interval_s",
    "ssl_warn_days",
    "retention_days",
    "process_interval_s",
    "process_max_rows",
    "font_size",
}
_FLOAT_KEYS = {"website_timeout_s"}
_BOOL_KEYS = {
    "sound_enabled",
    "desktop_enabled",
    "webhooks_enabled",
    "limit_resources",
    "update_check_enabled",
}
_STR_KEYS = {"theme_color", "theme_font"}
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
}

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
