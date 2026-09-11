from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from pulse_hwm import APP_NAME

PROJECT_MARKER = "pyproject.toml"


def _repo_root() -> Path:
    """Dev mode: repo root (marker file). Packaged mode: executable dir."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / PROJECT_MARKER).exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent


REPO_ROOT = _repo_root()


def data_dir() -> Path:
    """Database/config home. %LOCALAPPDATA%/PulseHWM, or ./data when portable."""
    if _portable_mode():
        return repo_next_to_exe_or_primary() / "data"
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return REPO_ROOT / "data"


def repo_next_to_exe_or_primary() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return REPO_ROOT


def _portable_mode() -> bool:
    """Portable mode if a data/ folder sits next to the (frozen) binary."""
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else REPO_ROOT
    return (root / "data").exists()


def load_env() -> None:
    """Load .env from dev root or packaged exe dir. Requires .gitignore'd .env (secrets policy)."""
    candidates = [
        REPO_ROOT / ".env",
        data_dir().parent / ".env",
    ]
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return
    load_dotenv(override=False)


@dataclass
class EnvConfig:
    """Secrets ONLY. Values come from .env / process env. Never print these."""

    discord_webhook_url: str = ""
    slack_webhook_url: str = ""
    alert_sound_enabled: bool = True

    @classmethod
    def from_env(cls) -> "EnvConfig":
        return cls(
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", "").strip(),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", "").strip(),
            alert_sound_enabled=os.environ.get("ALERT_SOUND_ENABLED", "true")
            .strip()
            .lower()
            != "false",
        )


@dataclass
class Settings:
    """User-facing defaults. UI (Settings tab) overrides these in the DB."""

    hardware_interval_ms: int = 1000
    website_interval_s: int = 30
    retention_days: int = field(default=30)
    website_timeout_s: float = 10.0
    ssl_warn_days: int = 14
    sound_enabled: bool = True
    webhooks_enabled: bool = True


DB_PATH: Path | None = None
_env: EnvConfig | None = None


def env() -> EnvConfig:
    global _env
    if _env is None:
        load_env()
        _env = EnvConfig.from_env()
    return _env


def reload_env() -> EnvConfig:
    global _env
    _env = None
    return env()


def ensure_dirs() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
