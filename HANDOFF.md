# HANDOFF — Pulse-HWM session resume (written 2026-09-11)

Read this first. It contains everything the next session needs to continue exactly where we left off.

## Repo state

- Repo: `C:\Users\Ayman Khaled\Documents\Pulse-HWM` (Windows, PowerShell 5.1, venv `.venv\Scripts\`)
- App: native Windows desktop "hardware monitor" — Python 3.13 + PySide6/Qt6, psutil, pythonnet. Retro 8-bit pixel dashboard. Author is a beginner learning as they go (see `AGENTS.md` for full conventions — read it).
- Current branch: **`feature/restart-as-admin`** (forked from `fix/error-sound-tone`, so it already contains the sound commit).
- Commits on main: `376bd34` = `chore: bump version to 0.1.1` (this is release v0.1.1, exe already built and verified earlier: `dist\PulseHWM\PulseHWM.exe`).
- Commit messages use `feat:` / `fix:` / `chore:` prefixes.
- **Uncommitted work exists right now** on this branch for the RESTART AS ADMIN feature (done, tested — just never committed because the agent hit its step limit mid-flow).

## Branches / commit graph

```
main:    2093550 (chore: AGENTS.md + black/ruff hooks) → 650e7f2 (feat: PROCESSES tab) → 376bd34 (chore: bump v0.1.1)
                                                  ↑ merged already
fix/error-sound-tone:   69bd031 (fix: error sound now descending womp womp, not happy ta-da)   ← branched from 376bd34, committed
feature/restart-as-admin: (same 69bd031 base) + UNCOMMITTED changes below                ← CURRENT branch
```

## UNCOMMITTED changes on feature/restart-as-admin (all done + verified, just commit them)

1. **`pulse_hwm/util.py`** — added two functions (plus imports `os`, `pathlib.Path` — verify they're at top):
   - `restart_command() -> tuple[str, str]` — pure helper: frozen build returns `(sys.executable, "")`; dev mode returns `(pythonw.exe in venv, "-m pulse_hwm")`. Verified live: `('C:\\Users\\Ayman Khaled\\Documents\\Pulse-HWM\\.venv\\Scripts\\pythonw.exe', '-m pulse_hwm')`.
   - `shell_runas(exe, args) -> bool` — ctypes `ShellExecuteW(None, "runas", exe, args, None, 1)`; returns `int(ret) > 32` (Windows "accepted" contract); returns False when `os.name != "nt"`. This is the injectable test seam.
2. **`pulse_hwm/ui/settings_tab.py`** — `RESTART AS ADMIN` button added to the RESOURCES panel (tooltip: "Quit and relaunch elevated — needed for some temperature sensors"); handler `_restart_as_admin()` calls `restart_command()` + `shell_runas()`, on success sets `self.test_result.setText("relaunching as ADMIN…")` then `QApplication.quit()` (imported locally); on failure/UAC-deny sets `self.test_result.setText("restart cancelled (UAC denied)")`. Order matters: current process must quit only AFTER Windows accepts elevation, so UAC doesn't pop over a dead app.
3. **`tests/test_settings_util.py`** — import line now includes `restart_command`; 3 new tests: `test_restart_command_frozen_execution` (monkeypatch `sys.frozen=True`, `sys.executable`), `test_restart_command_dev_mode_runs_module` (asserts `args == "-m pulse_hwm"`, exe endswith pythonw.exe/python.exe), `test_shell_runas_none_on_non_windows` (monkeypatch `util_mod.os.name = "posix"`).

**Verification already completed:** 65/65 pytest pass · black reformatted 1 file (already clean now) · `ruff check .` = "All checks passed!" · `restart_command()` smoke-run correct.

## Next actions (in order)

1. **Commit the restart-as-admin work:**
   ```powershell
   git add -A
   .venv\Scripts\pre-commit.exe run
   # if formatters rewrite files: git add -A again, rerun
   git commit -m "feat: RESTART AS ADMIN button on Settings RESOURCES panel"
   ```
   CRITICAL: the 2 secret scanners (`pulse custom secret scan (staged)`, `gitleaks (staged, via resolver guard)`) read the **staged** snapshot — always `git add` after any edit BEFORE running hooks. Never stage `.env`.
2. **Merge to main** (branch order is clean since restart-as-admin contains the sound commit):
   ```powershell
   git checkout main
   git merge feature/restart-as-admin --no-edit
   ```
   (Alternative: also merge `fix/error-sound-tone` first if you want it isolated — it's fast-forwardable from `376bd34`.)
3. **Optionally bump version to 0.1.2** (`pulse_hwm/__init__.py`, `__version__ = "0.1.2"`) — arguable: the two changes (new sound + restart btn) come after the 0.1.1 exe was built, so 0.1.2 is defensible. Ask the user.
4. **Rebuild exe** (user said earlier "lets build this exe" for v0.1.1 — if they want these two changes in the build):
   ```powershell
   .venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm
   # verify: dist\PulseHWM\PulseHWM.exe launches, title shows version
   ```
   Known harmless build warning: `WARNING: Ignoring non-existent resource pythonnet\runtime ... System.Xml.XmlSerializer.dll / netstandard.dll`.
5. **Optional live UAC check that was deliberately NOT done:** clicking RESTART AS ADMIN triggers a real UAC prompt on the user's screen. Pure seam is unit-tested; only test live with user's go-ahead. In dev mode it relaunches `pythonw -m pulse_hwm` elevated.

## Context: what was done this whole session (already committed)

- `chore/agents-md-and-hooks` branch: AGENTS.md, pyproject (black+ruff), pre-commit hooks, repo-wide format/lint cleanup. Committed `2093550`.
- `feature/processes-tab`: PROCESSES tab (grouped task-manager: PULSE-HWM / WINDOWS / SERVICES / APPLICATIONS / BACKGROUND), right-click END TASK (graceful→2s wait→forced kill) / KILL / COPY PID / OPEN FILE LOCATION, refuses PID 0/4/self; scans auto-pause when tab hidden (signal-based, queued across threads); resource knobs in Settings (scan interval, max rows, LOW PRIORITY toggle, TRIM MEMORY NOW, periodic 15-min trim). Hygiene fixes in websites collector (`_prune_state`) + dashboard temps in-place-refresh. 25 tests in `tests/test_processes.py`. Committed `650e7f2`, merged to main.
- `fix/error-sound-tone`: error sound in `alerts/notifier.py` replaced — was happy ascending 880→1174.7 Hz two-tone; now `ALERT_TONES` descending E5(0.10s)→C5(0.10)→G#4(0.10)→D#4(0.26 tail), square wave 8kHz. New `tests/test_alert_sound.py` (4 tests, incl. descending-shape guard test). Committed `69bd031`.

## Gotchas learned this session

- **Formatter authority = black** (ruff-format deliberately omitted; conflicts with black over blank lines before `if __name__`). ruff = lint only.
- **Cross-thread rule:** collectors run on worker QThreads; UI must signal them via queued Qt Signals (`active_changed`, `processes_reconfigure`), never direct method calls.
- PowerShell 5.1 chained native-exe stderr prints appear as scary `NativeCommandError` blocks — harmless, check `$?`.
- Tests must never touch real shell/processes/network; keep pure helpers in `util.py`/`processes.py` (no Qt) with injectable seams.
- If smoke-testing the app offscreen, remember smoke.py placeholder gotcha: any path placeholder (e.g. `PWD`) must be replaced with literal `C:\Users\Ayman Khaled\Documents\Pulse-HWM` before running.

## Verification commands cheat sheet

```powershell
.venv\Scripts\pytest.exe -q          # expect 65 passed
.venv\Scripts\black.exe pulse_hwm tests
.venv\Scripts\ruff.exe check .
.venv\Scripts\pre-commit.exe run     # after git add
python -m pulse_hwm                  # dev run (activate venv or use .venv\Scripts\python.exe)
.venv\Scripts\python.exe -m pulse_hwm --selftest
```
