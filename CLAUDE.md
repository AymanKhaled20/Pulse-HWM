# CLAUDE.md — Pulse-HWM

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
  app.py                    # composition root: builds services + controllers, --selftest
  config.py                 # .env loader, paths, EnvConfig/Settings dataclasses
  app_settings.py           # user settings persisted in DB (cached load/save/clamp)
  db.py                     # thread-safe SQLite wrapper (ALL reads+writes locked)
  logging_setup.py          # pulse.log / error.log handlers + crash hooks
  lifecycle.py              # ThreadGroup: start/stop the background QThreads
  single_instance.py        # one running instance; pulsehwm:// login-link handoff
  util.py                   # formatting helpers (bytes/rate/uptime/cpu name)
  processes.py              # process listing/classification/termination (no Qt)
  scheduler.py              # ONE jittered/back-off ticker for background jobs
  controllers/              # UI-thread QObjects gluing services to the UI
    rgb_controller.py       # builds the RGB stack, routes sensors/alerts/devices
    sync_controller.py      # login callbacks, session resume, applying sync pulls
    update_controller.py    # update check results -> banner/toast; install flow
    alert_relay.py          # moves site up/down transitions onto the UI thread
  collectors/               # telemetry, each runs on its own QThread
    hardware.py             # psutil + GPU/temps polling, HardwareThreadBridge
    websites.py             # HTTP checks + SSL expiry, WebsiteThreadBridge
    processes.py            # full process scan thread, ProcessesThreadBridge
    lhm.py                  # in-process LibreHardwareMonitorLib temp bridge
  cloud/                    # cloud backend (formerly auth/ — Worker, not Supabase)
    rest.py                 # CloudClient over the Worker (auth + sync + updates)
    oauth.py / pkce.py      # browser sign-in flow (PKCE verifier parked in Credential Manager)
    session.py / token_store.py  # sessions; refresh tokens in Credential Manager
    sync.py / sync_engine.py    # LWW merge plan + executor
    config.py               # publishable worker URL/key (non-secrets)
    updates/
      policy.py             # PURE update decisions (newer/rollback/hosts)
      trust.py              # Ed25519 manifest verify + WinVerifyTrust anchors
      checker.py / installer.py # Qt bridges: check + download/verify/spawn
  alerts/
    notifier.py             # tray/toast, synthesised sound, Discord/Slack webhooks
  rgb/                      # RGB lighting (see docs/RGB.md)
    model.py / layout.py    # Qt-free devices, colors, LED layouts
    effects/                # built-in + declarative (user JSON) effects, catalog
    manager.py              # PURE mode planner: off / effects / reactive / override
    engine.py               # renders frames per device (no Qt)
    worker.py               # engine on the "rgb-engine" QThread (all device I/O)
    sensors.py              # hardware snapshot -> reactive-effect sensor values
    assignment_store.py     # per-device effect assignments (JSON blob CRUD)
    drivers/                # RgbDriver contract, registry, openrgb/ (the ONLY transport)
  ui/
    main_window.py          # window, tabs, tray, close-to-tray behavior
    dashboard_tab.py        # live gauges/charts + top-processes panel
    processes_tab.py        # grouped process tree, right-click end task / kill
    sites_tab.py            # website list + status
    history_tab.py          # historical charts + event log
    settings_tab.py         # settings form (persists into DB)
    account_tab.py          # sign-in, sync status, update banner
    rgb_tab.py              # RGB mode, override, per-device assignments
    themes_tab.py           # color/font theme picker
    theme_manager.py / palettes.py  # live theme switching + palette definitions
    theme.py / theme.qss    # colors, fonts, pixel icon drawing, app stylesheet
    widgets/                # reusable pixel widgets (charts, gauges, panels, scanline)
  assets/                   # fonts, icons, vendored lhm_runtime
  workers/                  # (repo root) Cloudflare Worker: src/lib + src/routes, D1 + R2
scripts/                    # secret scanners, update_signing.py, publish_release.py
tests/                      # pytest suite
installer/                  # Inno Setup script (+ CI-generated version.iss)
.github/workflows/          # ci.yml (tests/audit), release.yml (installer builds)
pulse_hwm.spec              # PyInstaller onedir build spec
requirements.txt            # runtime deps
requirements-dev.txt        # dev/test/build deps
pytest.ini                  # pytest config
CHANGELOG.md                # single source of release notes (drive the banner)
docs/                       # ACCOUNTS.md (cloud), UPDATES.md (update runbook)
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
- Connect cross-thread signals to a **QObject's `@Slot` method**, never to a
  plain function/lambda or a non-QObject's method: Qt runs those on the
  EMITTING thread, which is how worker threads ended up touching widgets.
  Put UI-side glue in a controller (`controllers/`).
- Logging: `log = logging.getLogger("pulse.<area>")`; never `print()` or
  hand-written log files. Level via `PULSE_LOG_LEVEL` in `.env`.
- Optional hardware probes must never raise: catch broadly and return
  `None`/`N/A` so the UI degrades gracefully instead of crashing.
- All database access goes through the locked `Database` wrapper (collectors
  run off-thread; SQLite is opened with `check_same_thread=False` + WAL).
- Errors: UI shows placeholder/muted text when a source is unavailable.
  `logging_setup.py` installs crash hooks that log tracebacks (main thread,
  Qt slots, Python threads) to `%LOCALAPPDATA%\PulseHWM\error.log`; all
  log lines go to `pulse.log` there. Never log or print secret values.
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
- Only open a pull request when the user explicitly asks for one — otherwise
  commit straight to the feature branch and merge to `main` only after the user
  gives the OK. Small changes (palette tweaks, version bumps, minor fixes) are
  always PR-less.
- Before every commit, run the hooks: `pre-commit run --all-files`.
- All hooks must pass. If a formatter rewrites files, re-stage and re-run until
  the run is clean.
- Only commit when the user explicitly asks. Never stage `.env`.
- End-of-feature workflow: ALWAYS rebuild the exe with
  `.venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm` after finishing a
  feature/update (the user tests the `dist\PulseHWM\PulseHWM.exe` build as their prod
  version), but ASK before committing/pushing/merging to main. Note: a running
  (often admin-elevated) PulseHWM instance locks the exe and can make the
  rebuild fail with access-denied — close/kill it first.

## Releases & update channel
- VERSION SOURCE: `pulse_hwm/__init__.py` is the ONLY place. CI generates
  `installer/version.iss` and fails a release if tag != `__version__`. Never
  hand-edit `version.iss` except a routine bump; never let the two names drift.
- Releases are installer-ONLY (`PulseHWM-Setup-<v>.exe`, built by CI on tag
  push). Do not publish portable zips; don't reference source archives.
- Publishing (CI: `.github/workflows/release.yml`) publishes metadata to the
  worker which notifies registered+active installs; see `docs/UPDATES.md`.
  Update banners render the CHANGELOG `[x.y.z]` section verbatim — write it
  for users.
- Update trust anchors live in `pulse_hwm/cloud/updates/trust.py`
  (`TRUSTED_UPDATE_KEYS`, `AUTHENTICODE_REQUIRED`). A change there is a
  security change — full test suite + reviewer eyes required.

## Working style
- I'm a beginner learning as I go: favor clear, well-commented code over
  clever or compressed code. Give functions and variables meaningful names.
- Briefly explain any non-obvious decision in a comment (why, not just what).
- Keep changes small and focused; explain tricky logic in plain language.
