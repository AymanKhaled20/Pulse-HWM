# Pulse-HWM changelog

All notable changes to Pulse-HWM. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versioning
is [semver](https://semver.org/): PATCH = bug fixes only, MINOR = features /
architecture, MAJOR = breaking changes to settings, database, or sync
contracts.

The `## [x.y.z]` section of a release is machine-extracted and published as
both the GitHub release body and the in-app update banner — write for users.

## [1.3.0] - 2026-09-16
### Added
- **RGB controller with a native hardware driver** — Pulse now drives the
  keyboard lighting itself, with no vendor software behind it (works without
  and beats vendor apps when both try to steer). Verified live on an AULA
  F75; mode switch (`off / effects / reactive / override`) decides who owns
  the hardware, and OVERRIDE forces one look everywhere and keeps re-asserting
  it so Mystic Light / iCUE / Synapse cannot take devices back.
- **RGB tab** with per-device effect assignment, pixel color picker,
  brightness/speed (a render-rate setting caps CPU use at `rgb_engine_fps`),
  and status strip.
- **Effect library**: built-in STATIC / BREATHE / REACTIVE TEMP, plus
  custom effects imported as **declarative JSON** — by file, by paste, or by
  HTTPS URL (validated, size-capped; URL fetch is off by default).
- **Reactive lighting**: CPU/GPU temperature drives a cool→hot gradient
  (green→red), and error alerts strobe the devices, reverting
  automatically when the hold expires.
- **PLUGIN slots for six ecosystems**: Aula F75 (native, verified),
  Logitech G, Razer Chroma, Corsair iCUE, MSI Mystic Light, ASUS Aura
  (detection stub). Each appears only when the ecosystem's software/hardware
  is present; missing apps show an install hint instead of failing.
- **Settings rework**: the Settings tab is now a compact left rail with
  stacked sections — no two-column cramming; sections keep your
  expand/collapse choice.
- Headless self-test now probes RGB hardware (lists devices, sets one test
  color) so setup problems are visible without the GUI.

### Changed
- `rgb_*` settings are device-local: they never sync to the cloud (other
  machines' device names are meaningless elsewhere).
- RGB rendering runs on its own "rgb-engine" thread; hardware I/O never runs
  on the UI thread (same collector pattern as the hardware/websites tabs).

### Notes
- RGB settings persist in the standard local settings database.
- Third-party attributions for the SinoWealth protocol research and vendor
  SDKs live in `THIRD_PARTY.md`.

## [1.2.0] - 2026-09-16
### Added
- **In-app update system** for Pulse members: signed-in accounts are notified
  when a new version goes live and install it with one click — silently
  downloaded from Pulse's private storage, verified (SHA-256 + Ed25519
  release signature + Windows Authenticode), then applied without ever
  opening a browser.
- UPDATE banner on the ACCOUNT tab, [INSTALL NOW] / [LATER], download
  progress, and a tray "CHECK FOR UPDATES" action.
- "LOG IN TO GET THE LATEST OF PULSE — INCLUDING SECURITY FIXES" call-to-action
  for signed-out users: updates are a member benefit, and dormant accounts
  (inactive ~90 days) are asked to sign back in first.
- Single shared scheduler for all background jobs (jitter + backoff instead
  of independent QTimers), so check-ins never pile up on the cloud.

### Changed
- `pulse_hwm/auth/` renamed to `pulse_hwm/cloud/` and `SupabaseClient` renamed
  to `CloudClient` — honestly named now that the backend is a Cloudflare
  Worker + D1 (identical API shapes, no behavioral change).
- The cloud Worker was split into reviewable modules (lib/ + routes/) with
  the same endpoints; new `/updates/latest`, `/updates/publish`, `/dl/` routes.
- Releases are now **Windows installer only** (`PulseHWM-Setup-x.y.z.exe`),
  built automatically by CI on a version tag; version lives in one place
  (`pulse_hwm/__init__.py`) — the installer copy is generated.

### Fixed
- Close-to-tray toast crashed when reading settings (`self._db` was never
  assigned on the main window).

### Security
- Update manifests are Ed25519-signed in CI with a key the cloud server
  never holds; installers are hash-pinned and Authenticode-gated before run,
  with anti-downgrade (client + server) and download host allowlists.

## [1.1.6] - 2026-09-15
### Fixed
- Alert toggles are genuinely honored: saved 8-BIT SOUND / DESKTOP TOAST /
  WEBHOOK states now load at boot instead of resetting every restart.
- Toggles apply instantly on click — no APPLY button needed.
- TRIM MEMORY and minimized-to-tray toasts respect the DESKTOP TOAST toggle.
- Bundled the missing certifi CA bundle that crashed startup on some machines.

## [1.1.5] - 2026-09-14
### Added
- Adaptive dashboard layout (stretch, no clipping); compact Processes tab.
- Pulse logo everywhere (app icon, installer, branded auth emails).
### Changed
- Worker auth hardening + generic 500s (review pass).
