from __future__ import annotations


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


def str_to_bool(value: str, default: bool = True) -> bool:
    return default if value == "" else value.strip().lower() not in ("false", "0", "no", "off")
