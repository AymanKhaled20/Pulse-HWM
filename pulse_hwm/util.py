from __future__ import annotations

import os
import sys
from pathlib import Path


def human_bytes(n: float | None) -> str:
    if n is None:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:,.1f} PB"


def human_rate(bps: float | None) -> str:
    if bps is None:
        return "N/A"
    return f"{human_bytes(bps)}/s"


def human_uptime(seconds: int) -> str:
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


_CPU_JUNK = ("Family", "GenuineIntel", "GenuineArm", "AuthenticAMD", "arm64")


def short_cpu_name(name: str | None) -> str:
    """Registry marketing name only when the input is a WMI-style junk string,
    or nothing is provided."""
    needs_source = not name or any(marker in name for marker in _CPU_JUNK)
    if needs_source and sys.platform.startswith("win"):
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            value, _kind = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            if value:
                return _clean_cpu(value)
        except OSError:
            pass
    return _clean_cpu(name)


def _clean_cpu(name: str | None) -> str:
    import re

    if not name:
        return "Unknown CPU"
    cleaned = (
        name.replace("(R)", "")
        .replace("(r)", "")
        .replace("(TM)", "")
        .replace("(tm)", "")
    )
    cleaned = re.sub(r"\s+CPU\s+@\s+.*$", "", cleaned)
    cleaned = re.sub(r"\s+@\s+.*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Unknown CPU"


def str_to_bool(value: str, default: bool = True) -> bool:
    return (
        default
        if value == ""
        else value.strip().lower() not in ("false", "0", "no", "off")
    )


def restart_command() -> tuple[str, str]:
    """The (exe, args) that relaunches Pulse, for the RESTART AS ADMIN button.

    Frozen builds re-execute the packaged exe itself; dev mode uses pythonw
    (windowless) from the venv so the relaunch looks like a normal boot.
    """
    if getattr(sys, "frozen", False):
        return str(sys.executable), ""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.exists() else str(sys.executable)
    # windowless AND identical to how `python -m pulse_hwm` boots
    return exe, "-m pulse_hwm"


def shell_runas(exe: str, args: str, cwd: str | None = None) -> bool:
    """Ask Windows to start `exe` elevated (UAC prompt). True when Windows
    accepted the launch — False when the user declined / on non-Windows.

    `cwd` is the working directory the NEW process starts in. Pass it for
    dev mode because `-m pulse_hwm` only resolves from the project root;
    if we relied on inherited cwd, relaunching from any other folder would
    fail with "No module named pulse_hwm".

    Tiny injectable seam so tests never touch the real shell.
    """
    if os.name != "nt":
        return False

    import ctypes

    SW_SHOWNORMAL = 1
    # >32 means success per the ShellExecute contract; small values = cancel.
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe, args, cwd, SW_SHOWNORMAL
    )
    return int(ret) > 32


def is_admin() -> bool:
    """True when THIS process is already running with administrator rights.

    Used to grey out RESTART AS ADMIN: relaunching elevated changes nothing
    if we're already elevated, so the button would only be a footgun.
    Never raises — False on non-Windows or if the check itself fails.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        # Same probe scripts/lhm.py uses; IsUserAnAdmin is formally
        # "deprecated" by Microsoft but still the cheapest reliable answer.
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def app_root() -> str:
    """Absolute path of the pulse_hwm package's parent (the project root).

    Used as the working directory when relaunching in dev mode.
    """
    return str(Path(__file__).resolve().parents[1])
