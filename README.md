# Pulse-HWM

A glorified hardware monitor â€” a small native Windows desktop app with high
hopes. Watches your machine (CPU, RAM, disk, network, GPU, temps, battery, top
processes), tracks the health of websites you care about (status, latency,
uptime, SSL expiry), and makes a noise when anything goes wrong.

Yellow and black pixel-art look by default, changeable live in the THEMES tab
(15 color palettes, 15 font sets, adjustable font size â€” all persisted).

## Features

- **Hardware** (1s polling): CPU/RAM/disks/network/GPU/temps/battery, top
  processes (Task-Manager style: every instance of an app nests under it,
  with combined RAM/CPU; click a header to sort by CPU/MEM/RAM)
- **Websites** (configurable checks): up/down + status, latency history,
  uptime %, SSL expiry warnings, keyword rules
- **Alerts**: tray + desktop toasts, 8-bit sound, Discord/Slack webhooks
- **History**: charts + event log, SQLite-backed retention
- **Accounts** (optional): sign in with email/password, Google, or GitHub;
  cloud-syncs your sites + portable settings (`RLS`-fenced Per-key
  last-write-wins). The app is 100% functional offline; accounts only add sync.
  Refresh tokens live in Windows Credential Manager, never files. See
  [docs/ACCOUNTS.md](docs/ACCOUNTS.md).

## Install (v1.1.0 â€” production)

Grab the latest release from the repo's
[**Releases** page](https://github.com/AymanKhaled20/Pulse-HWM/releases/latest)
â€” two downloads are provided:

| File | What it is |
|---|---|
| `PulseHWM-Setup-1.0.1.exe` | **Installer (recommended)** â€” one-click, Start-menu icon, optional desktop icon and "start at login" |
| `PulseHWM-v1.0.1-win64.zip` | Portable build â€” unzip anywhere and run `PulseHWM.exe` directly |

Steps:
1. Download **`PulseHWM-Setup-1.0.1.exe`** from the release assets.
2. Windows SmartScreen may say "unknown publisher" â€” click **More info â†’ Run
   anyway** (the app is unsigned because code-signing certificates cost money).
3. Follow the wizard (per-user install, no admin rights needed).
4. Launch **PulseHWM** â€” the icon docks to your system tray; closing the
   window minimizes there instead of quitting. Quit from the tray icon menu.
   Temperature sensing (temps tab/GPU) works best when you ALLOW the
   Administrator prompt on the startup prompt.

Requirements: Windows 10 / 11 (64-bit). No Python or other prerequisites â€”
everything is bundled.

## Getting started (from source)

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

Python 3.13 Â· PySide6 (Qt6) Â· pyqtgraph Â· psutil Â· httpx Â· SQLite Â·
LibreHardwareMonitorLib (vendored binaries, MPL-2.0 â€” see
[THIRD_PARTY.md](THIRD_PARTY.md)) Â· PyInstaller + Inno Setup.

## Project structure

```
pulse_hwm/     # app package (app, config, db, collectors/, alerts/, ui/, assets)
scripts/       # secret scanners, LHM bridge, icon builder
tests/         # pytest suite
installer/     # Inno Setup script
```
