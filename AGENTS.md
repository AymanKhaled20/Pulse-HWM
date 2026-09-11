# AGENTS.md — Pulse-HWM

## What this is
A native Windows desktop app (Python 3.13 + PySide6/Qt6) that monitors the
machine (CPU/RAM/disk/network/GPU/temps/battery/top processes) and the health
of websites you care about (status, latency, uptime, SSL expiry), then alerts
you via tray/desktop toast, an 8-bit sound, or Discord/Slack webhooks.

Audience: the author (a beginner learning as they go) and technically-minded
users who want a retro, pixel-art "hardware monitor" dashboard. Not a website.

## Environment & commands
- OS: Windows. Shell: PowerShell 5.1.
- Setup: `py -m venv .venv`, activate, `pip install -r requirements.txt -r requirements-dev.txt`
- Run: `python -m pulse_hwm`
- Headless self-test: `python -m pulse_hwm --selftest`
- Tests: `pytest` (config in `pytest.ini`; 30s per-test timeout)
- Before committing: `.env` must exist from `.env.example` and `pre-commit install` must have run.

## File / folder structure
```
pulse_hwm/                  # application package
  __init__.py               # APP_NAME, __version__
  __main__.py               # entry point -> app.run()
  app.py                    # QApplication bootstrap, thread wiring, --selftest
  config.py                 # .env loader, paths, EnvConfig/Settings dataclasses
  app_settings.py           # user settings persisted in DB (load/save/clamp)
  db.py                     # thread-safe SQLite wrapper, schema, retention
  util.py                   # formatting helpers (bytes/rate/uptime/cpu name)
  processes.py              # process listing/classification/termination (no Qt)
  collectors/               # telemetry, each runs on its own QThread
    hardware.py             # psutil + GPU/temps polling, HardwareThreadBridge
    websites.py             # HTTP checks + SSL expiry, WebsiteThreadBridge
    processes.py            # full process scan thread, ProcessesThreadBridge
    lhm.py                  # in-process LibreHardwareMonitorLib temp bridge
  alerts/
    notifier.py             # tray/toast, synthesised sound, Discord/Slack webhooks
  ui/
    main_window.py          # window, tabs, tray, close-to-tray behavior
    dashboard_tab.py        # live gauges/charts + top-processes panel
    processes_tab.py        # grouped process tree, right-click end task / kill
    sites_tab.py            # website list + status
    history_tab.py          # historical charts + event log
    settings_tab.py         # settings form (persists into DB)
    theme.py / theme.qss    # colors, fonts, pixel icon drawing, app stylesheet
    widgets/                # reusable pixel widgets (charts, gauges, panels, scanline)
  assets/                   # fonts, icons, vendored lhm_runtime
scripts/                    # scan_secrets.py, gitleaks_guard.py, lhm.py, build_icon.py
tests/                      # pytest suite
installer/                  # Inno Setup script
pulse_hwm.spec              # PyInstaller onedir build spec
requirements.txt            # runtime deps
requirements-dev.txt        # dev/test/build deps
pytest.ini                  # pytest config
```

## Conventions
- Start every module with `from __future__ import annotations`.
- Naming: `snake_case` functions/vars, `PascalCase` Qt classes, `UPPER_SNAKE`
  constants. Private helpers are prefixed with `_`.
- Keep pure logic (parsing, classification, formatting, HTTP checks) free of
  Qt so it can be unit-tested on its own. Qt lives in `ui/` and the
  `*ThreadBridge.attach(...)` classes.
- Heavy/blocking work runs on worker `QThread`s and reports back with Qt
  `Signal`s. Never do hardware/network I/O on the UI thread.
- Optional hardware probes must never raise: catch broadly and return
  `None`/`N/A` so the UI degrades gracefully instead of crashing.
- All database access goes through the locked `Database` wrapper (collectors
  run off-thread; SQLite is opened with `check_same_thread=False` + WAL).
- Errors: UI shows placeholder/muted text when a source is unavailable. A
  global `sys.excepthook` logs tracebacks to
  `%LOCALAPPDATA%\PulseHWM\error.log`. Never log or print secret values.
- Tests: pytest. Prefer pure functions and dependency injection
  (`httpx.MockTransport` for websites, fake process factories for tasks).
  Tests must never hit the network or terminate real processes, and must stay
  under the 30s timeout.
- Formatting/lint: `black` + `ruff` (format and check). All pre-commit hooks
  must pass before committing.

## Secrets rule (non-negotiable)
- NEVER hardcode API keys, tokens, webhook URLs, or passwords in code.
- All secrets live only in `.env` (loaded via `config.env()` / python-dotenv).
  Use `.env.example` (same names, empty values) as the template.
- Before any commit, verify `.gitignore` still contains `.env`, and never
  stage it. Two pre-commit scanners (`scripts/scan_secrets.py`,
  `scripts/gitleaks_guard.py`) block commits on detected secrets.
- Secrets are never printed, logged, or shown for debugging.

## Git & commit workflow
- Every new feature gets its own branch — never commit feature work straight to
  `main`. Use `feature/<short-name>` (e.g. `feature/processes-tab`), `fix/<name>`
  for fixes, and `docs/` or `chore/` for everything else.
- Before every commit, run the hooks: `pre-commit run --all-files`.
- All hooks must pass. If a formatter rewrites files, re-stage and re-run until
  the run is clean.
- Only commit when the user explicitly asks. Never stage `.env`.
- End-of-feature workflow: ALWAYS rebuild the exe with
  `.venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm` after finishing a
  feature/update (the user tests the `dist\PulseHWM.exe` build as their prod
  version), but ASK before committing/pushing/merging to main. Note: a running
  (often admin-elevated) PulseHWM instance locks the exe and can make the
  rebuild fail with access-denied — close/kill it first.

## Working style
- I'm a beginner learning as I go: favor clear, well-commented code over
  clever or compressed code. Give functions and variables meaningful names.
- Briefly explain any non-obvious decision in a comment (why, not just what).
- Keep changes small and focused; explain tricky logic in plain language.
