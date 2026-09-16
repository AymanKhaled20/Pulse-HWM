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

- F75 per-key index calibration capture
- Dongle PID `010D` support
- Vendor SDK drivers: Logitech → Razer → Corsair → MSI → ASUS
- URL effect import (implemented phase 14), settings rail rework (M6)
