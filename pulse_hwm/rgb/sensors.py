"""Hardware snapshot → reactive-effect sensor values (pure, no Qt).

The hardware collector emits a big snapshot dict; reactive RGB effects only
need four numbers. These keys are the reactive effect contract:
cpu_temp / gpu_temp / mem_pct / max_temp — each None when unavailable.
"""

from __future__ import annotations


def _hottest(snapshot: dict, label_part: str) -> float | None:
    """Highest temperature among sensors whose label contains label_part
    (e.g. "CPU" matches "CPU Package" and "CPU Core #1")."""
    temps = [
        row["temp"]
        for row in (snapshot.get("temps") or [])
        if label_part in str(row.get("label", "")).upper()
        and row.get("temp") is not None
    ]
    return max(temps) if temps else None


def sensors_from_snapshot(snapshot: dict) -> dict:
    cpu_temp = _hottest(snapshot, "CPU")
    gpu_temp = _hottest(snapshot, "GPU")
    known_temps = [t for t in (cpu_temp, gpu_temp) if t is not None]
    return {
        "cpu_temp": cpu_temp,
        "gpu_temp": gpu_temp,
        "mem_pct": (snapshot.get("mem") or {}).get("pct"),
        "max_temp": max(known_temps, default=None),
    }
