#!/usr/bin/env python3
"""Run gitleaks on staged changes, resolving the binary from any shell.

Why: winget installs gitleaks but freshly-spawned shells may have a stale PATH.
This guard tries, in order:
  1. `gitleaks` on PATH
  2. %LOCALAPPDATA%/Microsoft/WinGet/Links/gitleaks.exe
  3. glob %LOCALAPPDATA%/Microsoft/WinGet/Packages/*/gitleaks.exe

Set PULSE_GITLEAKS_REQUIRED=false to warn instead of failing when gitleaks
cannot be located at all.

Exit codes: 0 clean, 1 leaks found, 2 gitleaks unavailable or error.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from glob import glob
from pathlib import Path


def resolve_gitleaks() -> str | None:
    path = shutil.which("gitleaks")
    if path:
        return path
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    link = local / "Links" / "gitleaks.exe"
    if link.is_file():
        return str(link)
    candidates = glob(str(local / "Packages" / "*" / "gitleaks.exe"))
    return candidates[0] if candidates else None


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    requested_required = (
        os.environ.get("PULSE_GITLEAKS_REQUIRED", "true").lower() != "false"
    )
    binary = resolve_gitleaks()

    if binary is None:
        msg = "[gitleaks] NOT FOUND — install it: winget install gitleaks.gitleaks"
        print(msg)
        return 0 if not requested_required else 2

    args = [binary, "protect", "--staged", "--redact", "-v"]
    print(f"[gitleaks] scanning staged changes ({Path(binary).name})...")
    try:
        result = subprocess.run(args, check=False)
    except OSError as exc:
        print(f"[gitleaks] failed to run: {exc}")
        return 2

    if result.returncode != 0:
        print("[gitleaks] COMMIT BLOCKED — leaks detected (see report above).")
        return 1
    print("[gitleaks] clean ✔ no secrets in staged changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
