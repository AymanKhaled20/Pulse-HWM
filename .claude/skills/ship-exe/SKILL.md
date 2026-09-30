---
name: ship-exe
description: Rebuild the PyInstaller exe (dist\PulseHWM\PulseHWM.exe) the user tests as prod, then selftest it.
disable-model-invocation: true
---

# Rebuild the prod exe

Run these steps in order with the PowerShell tool. Stop and report at the
first failure; do not retry blindly.

1. **Free the exe lock.** A running (often admin-elevated) PulseHWM locks
   `dist\PulseHWM\PulseHWM.exe` and makes the build fail with access-denied:

   ```powershell
   Get-Process PulseHWM -ErrorAction SilentlyContinue | Stop-Process -Force
   ```

   If `Stop-Process` is refused (elevated instance), ask the user to close
   Pulse from the tray and wait for their go-ahead.

2. **Build:**

   ```powershell
   .venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm
   ```

   On failure, show only the last ~30 lines of output plus the first error.

3. **Selftest the built exe.** The exe is windowed, so stdout is lost; write
   the report to the scratchpad instead:

   ```powershell
   $out = Join-Path $env:TEMP "pulse-selftest.json"
   $p = Start-Process dist\PulseHWM\PulseHWM.exe -ArgumentList "--selftest","--selftest-out",$out -Wait -PassThru
   "exit=$($p.ExitCode)"; Get-Content $out
   ```

   Exit 0 = pass. Exit 2 = no sensors/temps found (report it; not a build
   failure).

4. **Report** in 2-3 lines: build OK/failed, selftest exit code, and anything
   notable in the JSON (e.g. `cpu_temps: 0`). Do not commit, push, or merge;
   CLAUDE.md requires asking first.
