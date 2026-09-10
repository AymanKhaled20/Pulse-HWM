# Pulse-HWM

A glorified hardware monitor — a small desktop app with high hopes. It watches
your machine (CPU, RAM, disk, network, GPU, temps, battery, top processes),
tracks the health of websites you care about (status, latency, uptime %, SSL
expiry), and makes a noise when anything goes wrong.

Not a website. A native Windows desktop app.

![status](https://img.shields.io/badge/status-v1_in_progress-FFD400)

## Features

**Hardware monitoring** (1s polling)
- CPU load (overall + per-core) and clock speed
- RAM + swap usage
- Disk usage and read/write throughput
- Network up/down throughput
- GPU utilization / VRAM (NVIDIA via `nvidia-smi` ecosystem; others show N/A)
- Temperatures (best-effort via LibreHardwareMonitor WMI; graceful N/A otherwise)
- Battery status
- Top processes by CPU / memory

**Website monitoring** (30s checks, configurable)
- Up/down detection + HTTP status code
- Response-time history and trend charts
- Rolling uptime percentage
- SSL certificate expiry warnings (< 14 days)
- Keyword/body match rules

**Alerts**
- Desktop notifications (system tray + toast)
- 8-bit style sound alert (state-transition only — no spam)
- Discord and/or Slack webhooks
- Tray icon reflects the worst current state (green / amber / red)

**Dashboard**
- Real-time pixel-art dashboard with history charts

## Design language

Yellow and black. Pixelated. Hard 1–2px borders, no rounded corners, chunky
offset shadows, CRT scanline overlay, segmented blocky gauges, blinking LEDs.

| Purpose | Color |
|---|---|
| Background | `#0A0A0A` |
| Panel | `#141414` |
| Primary (amber) | `#FFD400` |
| Highlight | `#FFE873` |
| Danger | `#FF3B30` |
| Success | `#9BE800` |

Fonts: [Silkscreen](https://fonts.google.com/specimen/Silkscreen) (labels) and
[VT323](https://fonts.google.com/specimen/VT323) (readable numbers), bundled in
`pulse_hwm/assets/fonts/`.

## Stack

- Python 3.13
- PySide6 (Qt6) — UI, tray, notifications
- pyqtgraph — real-time charts
- psutil — hardware telemetry
- httpx — website checks + webhooks
- SQLite (stdlib) — history & settings
- PyInstaller + Inno Setup — packaging

## Getting started

```powershell
git clone <this-repo>
cd Pulse-HWM

# 1. Environment + deps
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# 2. Secrets (see policy below)
Copy-Item .env.example .env   # then fill in webhook URLs if you use them

# 3. Commit-time secret scanners (one-time)
pre-commit install

# 4. Run
python -m pulse_hwm
```

Tests:

```powershell
pytest
```

## Configuration

Secrets live in `.env` (gitignored — never committed):

| Variable | Purpose |
|---|---|
| `DISCORD_WEBHOOK_URL` | Alert POST target (empty = disabled) |
| `SLACK_WEBHOOK_URL` | Alert POST target (empty = disabled) |
| `ALERT_SOUND_ENABLED` | Boot default for sound alerts |

Everything else (intervals, retention, site list, toggles) lives in the app's
**Settings** tab and survives restarts via the local SQLite database.

## Secrets policy

- Never hardcode keys/tokens in code. `.env` only, loaded via
  environment variables.
- `.env` is gitignored and must never be staged.
- `.env.example` carries the same names with placeholder values so anyone can
  run the project without exposing real secrets.
- Two independent scanners run **before every commit** and block on a hit:
  1. `scripts/scan_secrets.py` — custom scanner for provider key patterns,
     private keys, webhooks, and high-entropy strings (output redacted).
  2. `scripts/gitleaks_guard.py` — [gitleaks](https://github.com/gitleaks/gitleaks)
     on staged changes (`gitleaks protect --staged`).
- Secrets are never printed/logged, not even for debugging.

Install gitleaks (one-time): `winget install gitleaks.gitleaks`

## Project structure

```
Pulse-HWM/
├─ pulse_hwm/            # application package
│  ├─ app.py             # QApplication bootstrap, fonts, theme
│  ├─ config.py          # .env loader + defaults
│  ├─ db.py              # SQLite schema, retention
│  ├─ collectors/        # hardware.py, websites.py (worker threads)
│  ├─ alerts/            # tray/toast, sound, webhook dispatch
│  ├─ ui/                # tabs, pixel widgets, theme.qss
│  └─ assets/            # fonts, icons
├─ scripts/              # scan_secrets.py, gitleaks_guard.py, build tooling
├─ tests/                # pytest suite
├─ installer/            # Inno Setup script
└─ .pre-commit-config.yaml
```

## Roadmap

- [x] Phase 0 — repo hygiene, secret scanners, git hooks, README
- [x] Phase 1 — scaffold: themed window + tray
- [x] Phase 2 — hardware collectors + dashboard widgets
- [x] Phase 3 — website monitor + sites tab
- [x] Phase 4 — alerts (tray, sound, webhooks)
- [x] Phase 5 — history + settings, persistence + retention
- [x] Phase 6 — pixel theme polish + icons
- [x] Phase 7 — test suite (34 tests)
- [x] Phase 8 — packaging: PyInstaller exe (verified) + Inno Setup script
- [ ] Beyond — autostart on login (installer option exists), history export, macOS/Linux builds

### Build the installer

```powershell
.venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm          # → dist\PulseHWM\
python scripts\build_icon.py                                      # regenerate icon
# optional, requires Inno Setup 6:
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\pulse-hwm.iss
```
