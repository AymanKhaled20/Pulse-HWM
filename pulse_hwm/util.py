from __future__ import annotations

import sys


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
