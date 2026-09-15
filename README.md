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
  cloud-syncs your sites + portable settings (server-fenced per-user
  last-write-wins). The app is 100% functional offline; accounts only add sync
  and updates. Refresh tokens live in Windows Credential Manager, never files.
  See [docs/ACCOUNTS.md](docs/ACCOUNTS.md).
- **Secure updates** (member benefit): signed-in accounts are notified when a
  new version goes live and update in-app with one click — downloads come
  from the GitHub release assets by default, or private R2 storage when it
  is provisioned. Every installer is checked against the Ed25519
  release-signature + SHA-256 of the signed manifest before anything runs
  (Authenticode is additionally verified once release signing is enabled
  and `AUTHENTICODE_REQUIRED` flips), with anti-downgrade built in.
  See [docs/UPDATES.md](docs/UPDATES.md).

## Install (v1.2.0 — production)

Grab the latest release from the repo's
[**Releases** page](https://github.com/AymanKhaled20/Pulse-HWM/releases/latest)
— the release ships **one download**:

| File | What it is |
|---|---|
| `PulseHWM-Setup-<version>.exe` | **The Windows installer** — one-click, Start-menu icon, optional desktop icon and "start at login" |

Steps:
1. Download **`PulseHWM-Setup-<version>.exe`** from the release assets.
2. Windows SmartScreen may say "unknown publisher" — click **More info → Run
   anyway** (until code signing is provisioned; every release carries a
   verifiable `SHA256SUMS` + build provenance).
3. Follow the wizard (per-user install, no admin rights needed).
4. Launch **PulseHWM** — the icon docks to your system tray; closing the
   window minimizes there instead of quitting. Quit from the tray icon menu.
   Temperature sensing (temps tab/GPU) works best when you ALLOW the
   Administrator prompt on the startup prompt.
5. Once installed, updates arrive in-app: sign in on the ACCOUNT tab and
   click INSTALL NOW when notified — no manual downloads.

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
`.venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm` — though releases
are built automatically in CI when you push a `vX.Y.Z` tag (installer only).

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
