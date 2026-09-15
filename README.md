# Pulse-HWM

A glorified hardware monitor — a small native Windows desktop app with high
hopes. It watches your machine (CPU, RAM, disk, network, GPU, temps, battery,
top processes), tracks the health of websites you care about (status, latency,
uptime, SSL expiry), and makes a noise when anything goes wrong.

Yellow and black pixel-art look by default, changeable live in the THEMES tab
(15 color palettes, 15 font sets, adjustable font size — all persisted).

## Features

- **Hardware** (1 s polling): CPU/RAM/disks/network/GPU/temps/battery; top
  processes in a Task-Manager style tree (every instance of an app nests
  under it with combined RAM/CPU; click a header to sort by CPU/MEM)
- **Websites** (configurable checks): up/down + status code, latency history,
  uptime %, SSL expiry warnings, keyword rules
- **Alerts**: tray + desktop toasts, 8-bit sound, Discord/Slack webhooks —
  each toggle applies instantly and survives a restart
- **History**: charts + event log with SQLite-backed retention pruning
- **Accounts** (optional): sign in with email/password, Google, or GitHub;
  cloud-syncs your sites + portable settings (server-fenced per-user,
  last-write-wins). The app is 100% functional offline — accounts only add
  sync and updates. Refresh tokens live in Windows Credential Manager,
  never in files. See [docs/ACCOUNTS.md](docs/ACCOUNTS.md).
- **Secure updates** (member benefit): signed-in accounts are notified when a
  new version goes live and update in-app with one click — the installer is
  downloaded silently (GitHub release assets by default, private R2 storage
  when provisioned), then verified against the Ed25519 release-signature and
  the SHA-256 digest listed in the signed manifest before anything runs
  (Authenticode is additionally verified once release signing is enabled and
  `AUTHENTICODE_REQUIRED` flips). Anti-downgrade is built in on both the
  client and the update server. See [docs/UPDATES.md](docs/UPDATES.md).

## Install (v1.2.0 — production)

Grab the latest release from the repo's
[**Releases** page](https://github.com/AymanKhaled20/Pulse-HWM/releases/latest)
— the release ships **one download**:

| File                              | What it is                                                                        |
| --------------------------------- | --------------------------------------------------------------------------------- |
| `PulseHWM-Setup-<version>.exe`    | **The Windows installer** — one-click, Start-menu icon, optional desktop icon and "start at login" |

Steps:
1. Download **`PulseHWM-Setup-<version>.exe`** from the release assets.
2. Windows SmartScreen may say "unknown publisher" — click **More info → Run
   anyway** (until code signing is provisioned; every release carries a
   verifiable `SHA256SUMS` + build-provenance attestation).
3. Follow the wizard (per-user install, no admin rights needed).
4. Launch **PulseHWM** — the icon docks to your system tray; closing the
   window minimizes there instead of quitting (quit from the tray menu).
   Temperature sensing (temps + GPU) works best when you ALLOW the
   Administrator prompt at startup.
5. From then on, updates arrive **in-app**: sign in on the ACCOUNT tab and
   click **INSTALL NOW** when the banner appears — no browser, no manual
   downloads.

Requirements: Windows 10 / 11 (64-bit). No Python or other prerequisites —
everything is bundled.

## Getting started (from source)

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env      # add webhook URLs if you use them
pre-commit install
python -m pulse_hwm
```

Run tests with `pytest`. Build the exe with
`.venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm` — though releases
are built automatically in CI when you push a `vX.Y.Z` tag (installer only).

## Configuration

Secrets live only in `.env` (gitignored, never committed): `DISCORD_WEBHOOK_URL`,
`SLACK_WEBHOOK_URL`, `ALERT_SOUND_ENABLED`. Everything else (intervals,
retention, sites, theme) is configured live in the app's SETTINGS/THEMES tabs
and persisted in the local SQLite database.

Two secret scanners (`scripts/scan_secrets.py` and
[gitleaks](https://github.com/gitleaks/gitleaks)) run before every commit and
block on detected secrets. Never hardcode keys in code.

## The cloud backend (optional)

Accounts, sync, and the update channel run on a tiny—and fully replayable—
[Cloudflare Worker](workers/) (TypeScript-style ESM modules, D1 database;
private R2 bucket for installer hosting when provisioned). Its code, schema,
and runbooks live in this repo:

- `workers/` — the Worker: auth (password + PKCE email links + Google/GitHub
  OAuth), schema-fenced row-level sync, and the signed update channel
- `docs/ACCOUNTS.md`, `docs/UPDATES.md` — account/release runbooks
- CI builds installers and publishes update metadata on a `vX.Y.Z` tag push

## Stack

Python 3.13 · PySide6 (Qt6) · pyqtgraph · psutil · httpx · SQLite ·
LibreHardwareMonitorLib (vendored binaries, MPL-2.0 — see
[THIRD_PARTY.md](THIRD_PARTY.md)) · Cloudflare Workers + D1 · PyInstaller +
Inno Setup.

## Project structure

```
pulse_hwm/            # app package (app, config, db, collectors/, alerts/, ui/,
                      #   cloud/[auth+sync+updates], assets/, vendored LHM runtime)
workers/              # cloud backend: Cloudflare Worker (src/lib + src/routes), D1 schema
scripts/              # secret scanners, update_signing.py, publish_release.py, LHM bridge
tests/                # pytest suite
installer/            # Inno Setup script (+ CI-generated version.iss)
docs/                 # ACCOUNTS.md, UPDATES.md runbooks
.github/workflows/    # ci.yml (tests/audit), release.yml (installer builds + publish)
CHANGELOG.md          # single source of release notes (drives release body + banner)
```
