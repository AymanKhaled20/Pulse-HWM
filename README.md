# Pulse-HWM

A glorified hardware monitor — a small native Windows desktop app with high
hopes. Watches your machine (CPU, RAM, disk, network, GPU, temps, battery, top
processes), tracks the health of websites you care about (status, latency,
uptime, SSL expiry), and makes a noise when anything goes wrong.

Yellow and black pixel-art look by default, changeable live in the THEMES tab
(15 color palettes, 15 font sets, adjustable font size — all persisted).

## Features

- **Hardware** (1s polling): CPU/RAM/disks/network/GPU/temps/battery, top
  processes (Task-Manager style: every instance of an app nests under it,
  with combined RAM/CPU)
- **Websites** (configurable checks): up/down + status, latency history,
  uptime %, SSL expiry warnings, keyword rules
- **Alerts**: tray + desktop toasts, 8-bit sound, Discord/Slack webhooks
- **History**: charts + event log, SQLite-backed retention

## Getting started

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env   # add webhook URLs if you use them
pre-commit install
python -m pulse_hwm
```

Run tests with `pytest`. Build the exe with
`.venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm`.

## Configuration

Secrets live only in `.env` (gitignored, never committed): `DISCORD_WEBHOOK_URL`,
`SLACK_WEBHOOK_URL`, `ALERT_SOUND_ENABLED`. Everything else (intervals,
retention, sites, theme) is configured in the app's Settings/THEMES tabs and
persisted in the local SQLite database.

Two secret scanners (`scripts/scan_secrets.py` and
[gitleaks](https://github.com/gitleaks/gitleaks)) run before every commit and
block on detected secrets. Never hardcode keys in code.

## Stack

Python 3.13 · PySide6 (Qt6) · pyqtgraph · psutil · httpx · SQLite ·
LibreHardwareMonitorLib (vendored binaries, MPL-2.0 — see
[THIRD_PARTY.md](THIRD_PARTY.md)) · PyInstaller + Inno Setup.

## Project structure

```
pulse_hwm/     # app package (app, config, db, collectors/, alerts/, ui/, assets)
scripts/       # secret scanners, LHM bridge, icon builder
tests/         # pytest suite
installer/     # Inno Setup script
```
