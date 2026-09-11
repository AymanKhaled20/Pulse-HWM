from __future__ import annotations

import pytest

from pulse_hwm import app_settings
from pulse_hwm.db import Database
from pulse_hwm.util import (
    human_bytes,
    human_uptime,
    restart_command,
    short_cpu_name,
    str_to_bool,
)


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "settings.db")


def test_defaults_when_empty(db):
    values = app_settings.load(db)
    assert values.hardware_interval_ms == 1000
    assert values.website_interval_s == 30
    assert values.retention_days == 30


def test_save_field_roundtrip_survives_reload(db):
    """The whole point of save_field: flip the toggle, 'restart', it's still on."""
    app_settings.save_field(db, "limit_resources", True)
    assert app_settings.load(db).limit_resources is True
    app_settings.save_field(db, "limit_resources", False)
    assert app_settings.load(db).limit_resources is False


def test_save_field_rejects_unknown_key(db):
    with pytest.raises(ValueError):
        app_settings.save_field(db, "not_a_real_key", True)


def test_roundtrip(db):
    values = app_settings.AppSettings(
        hardware_interval_ms=500,
        website_interval_s=15,
        website_timeout_s=7.5,
        ssl_warn_days=21,
        retention_days=10,
        sound_enabled=False,
        desktop_enabled=True,
        webhooks_enabled=False,
    )
    app_settings.save(db, values)
    loaded = app_settings.load(db)
    assert loaded == values


def test_corrupt_value_falls_back(db):
    db.set_setting("hardware_interval_ms", "not-a-number")
    values = app_settings.load(db)
    assert values.hardware_interval_ms == 1000


def test_clamp():
    assert app_settings.clamp("hardware_interval_ms", 10) == 250
    assert app_settings.clamp("website_interval_s", 99999) == 3600


def test_human_bytes():
    assert human_bytes(0) == "0.0 B"
    assert human_bytes(500) == "500.0 B"
    assert human_bytes(1024 * 1024).endswith("MB")
    assert human_bytes(None) == "N/A"


def test_human_uptime():
    assert human_uptime(3_900) == "1h 5m"
    assert human_uptime(60) == "1m"
    assert human_uptime(2 * 86400 + 3) == "2d 0h 0m"


def test_str_to_bool():
    assert str_to_bool("true") is True
    assert str_to_bool("false") is False
    assert str_to_bool("", True) is True


def test_cpu_name_clean():
    assert short_cpu_name("Intel(R) Core(TM) i7 CPU @ 2.60GHz") == "Intel Core i7"
    name = short_cpu_name("")
    assert isinstance(name, str) and len(name) > 0


def test_restart_command_frozen_execution(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\\Apps\\PulseHWM.exe")
    assert restart_command() == (r"C:\\Apps\\PulseHWM.exe", "")


def test_restart_command_dev_mode_runs_module(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\\fake\\python.exe")
    # pythonw.exe never exists next to the fake path → falls back to it, but
    # the important invariant is: relaunch boots the same module
    exe, args = restart_command()
    assert args == "-m pulse_hwm"
    assert exe.endswith(("pythonw.exe", "python.exe"))


def test_shell_runas_none_on_non_windows(monkeypatch):
    import pulse_hwm.util as util_mod

    monkeypatch.setattr(util_mod.os, "name", "posix")
    assert util_mod.shell_runas("anything.exe", "") is False


def test_shell_runas_accepts_cwd_argument(monkeypatch):
    """The new `cwd` seam (dev-mode relaunch) must not change the non-Windows
    path — it short-circuits to False no matter what cwd is passed."""
    import pulse_hwm.util as util_mod

    monkeypatch.setattr(util_mod.os, "name", "posix")
    assert util_mod.shell_runas("anything.exe", "-m pulse_hwm", cwd=r"C:\tmp") is False


def test_app_root_is_project_root():
    from pathlib import Path

    from pulse_hwm.util import app_root

    # The package's parent must contain the package itself — that's the
    # working directory `-m pulse_hwm` needs to resolve from.
    assert (Path(app_root()) / "pulse_hwm" / "util.py").is_file()


def test_is_admin_false_on_non_windows(monkeypatch):
    """No elevation concept on POSIX — the check must short-circuit to False
    (that's what greys out RESTART AS ADMIN on non-Windows test machines)."""
    import pulse_hwm.util as util_mod

    monkeypatch.setattr(util_mod.os, "name", "posix")
    assert util_mod.is_admin() is False


def test_is_admin_never_raises():
    """On real Windows this probes the process token; whatever the answer,
    it must come back as a plain bool (UI depends on it never raising)."""
    from pulse_hwm.util import is_admin

    assert is_admin() in (True, False)
