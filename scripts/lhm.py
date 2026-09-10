#!/usr/bin/env python3
"""Pulse-HWM — LibreHardwareMonitor (MPL-2.0) management CLI.

  py scripts/lhm.py fetch    # one-time download of the runtime bundle
  py scripts/lhm.py probe    # print every temperature sensor found
  py scripts/lhm.py run      # launch the official LibreHardwareMonitor app
                             # (run-as-admin for full CPU temp coverage)

Pulse-HWM's collector auto-uses the in-process library once `fetch` has run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pulse_hwm.collectors.lhm import (
    LHM_ZIP_URL,
    is_available,
    lib_path,
    runtime_dir,
)


def fetch() -> int:
    if is_available():
        print(f"[lhm] already installed: {lib_path()}")
        return 0
    dest = runtime_dir()
    print(f"[lhm] downloading {LHM_ZIP_URL}")
    print(f"[lhm] -> {dest}")

    def progress(done: int, total: int) -> None:
        if total:
            pct = 100 * done // total
            sys.stdout.write(f"\r[lhm] {pct:3d}%  {done / 1e6:.1f} MB / {total / 1e6:.1f} MB")
            sys.stdout.flush()

    ok, message = _download(progress)
    print()
    if ok:
        print("[lhm] installed — Pulse-HWM will use LibreHardwareMonitor sensors in-process")
        return 0
    print(f"[lhm] FAILED: {message}")
    return 2


def _download(progress) -> tuple[bool, str]:
    from pulse_hwm.collectors.lhm import download_to
    return download_to(runtime_dir(), on_progress=progress)


def probe() -> int:
    if not is_available():
        print("[lhm] runtime not installed — run: py scripts/lhm.py fetch")
        return 2
    from pulse_hwm.collectors.lhm import LibreSensors

    sensors = LibreSensors()
    rows = sensors.read()
    if rows is None:
        print(f"[lhm] no sensors available ({sensors.last_error or 'unknown'})")
        return 2
    for row in rows:
        print(f"  {row['temp']:6.1f} C   {row['label']}")
    note = "" if _is_admin() else "  (run app/scripts as ADMIN for CPU core temp access)"
    print(f"[lhm] {len(rows)} temperature sensor(s) read{note}")
    return 0


def run() -> int:
    exe = runtime_dir() / "LibreHardwareMonitor.exe"
    if not exe.exists():
        print("[lhm] official app not downloaded — fetching full bundle first...")
        if fetch() != 0:
            return 2
    subprocess.Popen([str(exe)], cwd=str(runtime_dir()))
    print(f"[lhm] launched {exe}")
    print("[lhm] Pulse-HWM will read sensors from it via the WMI bridge too")
    return 0


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def status_json() -> int:
    info = {
        "available": is_available(),
        "lib": str(lib_path()),
        "admin": _is_admin(),
    }
    print(json.dumps(info))
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "fetch":
        return fetch()
    if cmd == "probe":
        return probe()
    if cmd == "run":
        return run()
    if cmd == "status":
        return status_json()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
