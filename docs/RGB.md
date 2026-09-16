# Pulse RGB — engineering log

Running doc for the RGB engine phases. Each phase leaves a short note here so
future work (and reviewers) never have to reconstruct context from diffs.

## Status

| Phase | Branch | State |
|---|---|---|
| M1 | `feature/rgb-model` → … → `feature/rgb-modes` | DONE |
| M2 | `feature/rgb-aula-protocol` → `feature/rgb-aula-transport` | DONE (hw-verified) |
| M3 | `feature/rgb-tab-shell` … `feature/rgb-effect-url` | DONE |
| M4 | `feature/rgb-reactive-temp` → `feature/rgb-reactive-alert` | DONE (hw-verified) |
| M5 | `feature/rgb-driver-logitech` … `feature/rgb-driver-asus` | DONE |

## Vendor driver matrix (as implemented)

| Driver | Backend | Brightness | Per-LED | Verify |
|---|---|---|---|---|
| `aula_f75` | hidapi 0x06 direct (feature reports) | via frame scale | YES (126) | ✅ owner-verified |
| `logitech` | ctypes LogitechLED.dll (G HUB/LGS) | — | whole-device | needs G HUB |
| `razer_chroma` | Chroma REST localhost:54235 + heartbeat | — | whole-device | needs Synapse |
| `corsair_icue` | cuesdk ctypes binding | — | whole-setup | needs iCUE |
| `msi_mystic` | ctypes MysticLight_SDK.dll | — | 16-slot palette | needs MSI Center |
| `asus_aura` | probe-only stub (COM surface later) | — | — | blocked: no hw |

Color-mixing note (from owner's hardware test): the F75 renders mid-gradient
values (e.g. yellow/orange lerp between green and red) with a washed-out,
near-white appearance — an LED mixing artifact. Compact output palette is a
tracked follow-up (docs listed in Deferred).

## Architecture in one breath

Qt-free model (`rgb/model.py`) → effects render **frames** (one color per
LED) → pure mode planner (`rgb/manager.py`) decides who drives the hardware
(`off / effects / reactive / override`) → engine (`rgb/engine.py`) ticks at
`rgb_engine_fps` → driver (`rgb/drivers/`) pushes frames to hardware via
OpenRGB-like per-LED contracts or vendor SDKs. Settings persist in the
standard `settings` table; `rgb_*` keys are device-local and never synced.

## AULA F75 (native HID driver)

- VID:PID `258A:010C` (wired), `258A:010D` (dongle; phase 25 follow-up).
- 20-byte output reports, report id 0x13, checksum `sum(0..18) & 0xFF`.
- Effect change: read(1) → write config(10) → palette(37) → save(1).
  Every fragment must be echoed before the next is sent (driver drains
  echoes best-effort).
- **Byte-14 quirk**: config fragment 0, apply flag MUST be `0x00` on write
  (firmware flips it to `0x01` after applying; copying it back no-ops the
  whole write).
- Effect 1 (`Fixed_on`) + custom color palette is the uniform-color path —
  what `set_frame()` uses today.
- Per-key (`Self_define`, effect 21, planar R/G/B 126-LED map) is fully
  encoded (`aula_protocol.perkey_fragments`) but dead until the F75
  key→LED-index map is calibrated on hardware.

## Manual hardware checklist (runner: the hardware owner)

**VERIFIED 2026-09-16 on real F75 hardware (wired, no vendor software):**
the direct-mode channel drives all keys — RED → OFF → GREEN sequence
confirmed by the hardware owner. Note for future sessions: hidapi's
`usage_page` is unreliable here; the correct channel is found by probing
`get_feature_report(0x06, 520)` (the only collection that answers is the
configurator channel). OpenRGB SinowealthKeyboard10cController frame lands
verbatim.

Full checklist (re-run after each driver change):

    .venv\Scripts\python.exe -m pulse_hwm --selftest

- [x] Keyboard visibly went RED → OFF → GREEN via direct mode
- [x] Reverts cleanly when the channel is released
- [x] No crash / no phantom behavior on release
- [ ] (pending) `--selftest` integration probe reports `aula_frame_set`
- [ ] (pending) Unplug mid-run → clean degradation, no crash

## Deferred (tracked, planned)

- **Uninstall Logitech LGS before starting the vendor-free phase** (installed 2026-09-16 via winget for live driver verification). winget `Logitech.LGS`; remove after each driver's vendor-free replacement lands.
- **Vendor-free RGB drivers (no LGS/iCUE/Synapse/MSI Center needed)** — OpenRGB-style raw per-device HID drivers per model; needs USB captures from the owner's hardware for each device family. Parked AFTER v1.3.0 on purpose: it reverses the vendor-SDK dependency per ecosystem. Logitech currently runs via LGS (installed, thin); iCUE/MSI Center installs were declined by the owner as bloatware.
- F75 per-key index calibration capture
- Dongle PID `010D` support
- Compact F75 output palette (mid-gradients render washed-out/whitish — see Color-mixing note)
- URL effect import (implemented), settings rail rework (M6)


## Vendor-free phase — P1 (raw HID layer) 2026-09-17

Branch feature/rgb-vendor-free. LGS uninstalled (owner kept G HUB);
iCUE/MSI Center were never installed — software purge complete.

Ground truth from scripts/rgb_raw_scan.py on the owner machine (no vendor
software): 19 reachable vendor interfaces. Key channels:
- razer 1532:0537 usage page 0xFFA0 (interface 3) — the RGB control slot
- logitech 046D:C092 usage page 0x0059 (HID++) — opens clean, G HUB coexists
- msi 1462:7D41 — HID interface present but EC protocol not public
- corsair: none present (hardware unplugged)

Shipped P1: drivers/raw/{base_raw,razer_protocol,razer_raw,logitech_raw,
corsair_raw,msi_raw} + registry order aula -> raw -> SDK + settings gate
rgb_allow_raw_protocols (default OFF). Detection works everywhere; WRITE
paths fail closed with a live reason until captures validate them.

Deferred premiums: (1) razer matrix row protocol: capture vs envelope,
report id; (2) logitech LED feature list discovery (feature 0x0000 walk);
(3) corsair handshake/frame cheat sheet — blocked until a device is plugged
in; (4) msi stays detect-only this release.

