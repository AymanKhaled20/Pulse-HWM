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

## Driver matrix (current)

| Driver | Backend | Per-LED | Verify |
|---|---|---|---|
| `aula_f75` | hidapi 0x06 direct (feature reports) | YES (126) | ✅ owner-verified |

**2026-09-22 pivot — vendor-SDK + raw-HID drivers removed.** The
`logitech`/`razer_chroma`/`corsair_icue`/`msi_mystic`/`asus_aura` SDK drivers
(bloatware requirement) and the vendor-free raw-HID first-pass experiment are
gone. Non-AULA RGB is now delegated to a **headless OpenRGB fork** run as a
separate process and driven over its documented SDK protocol on loopback —
see "OpenRGB backend" below. Reason: OpenRGB already reverse-engineered the
per-device protocols (MSI Mystic Light HID 0x7D41, Razer, Corsair, Logitech
G203L) we would otherwise have to capture and validate by hand, and stock
OpenRGB even disables the MSI detector until its detection macro is
re-enabled at build time — hence our fork.

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
- Compact F75 output palette (mid-gradients render washed-out/whitish — see Color-mixing note)
- OpenRGB matrix-map layouts (currently devices render linearly; keyboard
  matrix positions would need the upstream matrix-map parser)
- Native OpenRGB brightness (mode UPDATEMODE) — engine color-scaling for now

## OpenRGB backend — Gate A PASSED (2026-09-22)

The fork CI ([AymanKhaled20/OpenRGB](https://github.com/AymanKhaled20/OpenRGB),
branch `pulse-headless`, loopback-bound server) built clean and the artifact
enumerated the owner's machine via OUR protocol-5 client:

| Device | OpenRGB read |
|---|---|
| MSI MAG B660 TOMAHAWK WIFI DDR4 (MS-7D41) | 122 LEDs, zones JRGB1/JRGB2/JRAINBOW1(60)/JRAINBOW2(60), **HID transport — no PawnIO/admin** |
| Razer Kraken V3 X (1532:0537 = headset!) | 1 whole-device zone |
| Logitech G203 Lightsync | 3-LED mouse zone |
| AULA F75 (Sinowealth) | 90-LED keyboard matrix — visible, but stays EXCLUDED while the native F75 driver is healthy (`exclude_vids = {0x258A}`) |

Implementation shipped on `feature/rgb-openrgb-backend`:
`rgb/drivers/openrgb/{protocol,client,server,driver}.py` + `CompositeDriver`
routing `openrgb:<n>` frames to OpenRGB and `aula_f75:0` frames to the native
driver behind the single-driver engine contract. Settings:
`rgb_openrgb_enabled` (default on), `rgb_openrgb_port` (6742),
`rgb_openrgb_path` (override). Backend binaries bundled in
`pulse_hwm/assets/openrgb/` (+ OpenRGB-LICENSE.txt, SOURCE.txt); CI smoke
test = headless server listens on 127.0.0.1:6742 before artifact upload.
