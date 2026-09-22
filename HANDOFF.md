# HANDOFF — Pulse-HWM (read me after compaction)

> Written 2026-09-22 for the next agent context. If facts and code disagree,
> TRUST THE CODE and update this file.

## ACTIVE SESSION (2026-09-22) — OpenRGB backend pivot — CURRENT STATE

The Strix security scan is ON HOLD indefinitely: machine prep is complete
(strix 1.6.2, Docker Desktop 29.8.0 + WSL2 running, LLM env vars survive
reboot), but every model call hits `402 Insufficient account funds` on Zen
and the owner declined to fund it (they pay for opencode Go, which rejects
third-party clients). Options for later: Strix Router free credit, OpenRouter
free tier, or Ollama local. `.strixignore` is ready at repo root.

**Direction decided instead: OpenRGB as the RGB backend.** Owner: "use the
backend of openrgb and build our own ui and ux." Research + decisions locked
(see docs/RGB.md "2026-09-22 pivot"):

- Fork `CalcProgrammer1/OpenRGB` (GitHub mirror) → `AymanKhaled20/OpenRGB`,
  branch `pulse-headless`, minimal patches: re-enable the MSI Mystic Light
  `MS-7D41` HID detector (commented out upstream), loopback-only SDK server
  bind, disable plugin autoload. Sinowealth stays DISABLED upstream (bricking
  history) so the AULA F75 keeps our hardware-verified native driver.
- Build headless in GitHub Actions (windows-latest + OpenRGB-Qt-Packages Qt
  6.8.3 + `scripts\build-windows.bat 6.8.3 2022 64`), consume the artifact —
  owner installs NOTHING. MSI board RGB is HID, not SMBus → NO PawnIO/admin.
  PawnIO+admin only needed for RGB RAM/other SMBus gear (prompted, opt-in).
- Gate A (go/no-go): artifact `openrgb.exe --list-devices` must show MSI
  7D41, Logitech G102 (0xC092 — natively supported), Razer 1532:0537,
  Corsair. Then our own SDK client (protocol ids on loopback 6742; UPDATELEDS
  1050 = our per-LED frame contract) as `OpenRgbDriver` +
  `CompositeDriver([OpenRgbDriver(), AulaDriver()])` so app.py's single-driver
  attach is untouched. Bundled in the installer with GPL-2.0 notice + source
  link (process separation = aggregation; fork keeps compliance simple).

### Done this session
- **Closed the 8 superseded PRs**: #36, #34, #30, #28, #27, #26, #25, #29
  (comments point at the pivot). All other PRs stay open for owner review.
- **Deleted abandoned branches** (local + remote):
  `feature/rgb-driver-kraken`, `feature/rgb-vendor-free`,
  `feature/rgb-driver-lightsync`, and untracked `scripts/kraken_discovery.py`
  (they held raw-HID/lightsync code; owner: "no dead code").
- **`chore/rgb-remove-vendor-sdk-drivers` @ 996a715**: removed the 5
  vendor-SDK drivers + their tests; app.py = AulaDriver only.
- **Fork live at [AymanKhaled20/OpenRGB](https://github.com/AymanKhaled20/OpenRGB)**
  branch `pulse-headless`: loopback-only SDK server bind (`net_port.cpp`),
  GH Actions `windows-headless-build` workflow green on the FIRST run
  (windows-latest + OpenRGB-Qt-Packages Qt6.8.3 + qmake/jom + windeployqt +
  a smoke step that requires the server listens on 127.0.0.1:6742).
  **MSI detectors are already re-enabled upstream — no macro patch needed**;
  and MSI board RGB is HID (no PawnIO/admin).
- **GATE A PASSED on owner hardware** (our own client, loopback 6742):
  MSI MAG B660 TOMAHAWK WIFI DDR4 MS-7D41 (122 LEDs, HID), Razer Kraken V3 X
  (1532:0537 = HEADSET — "kraken" mystery solved), Logitech G203 Lightsync,
  and the AULA F75 (Sinowealth, 90 LEDs — but excluded while native driver
  is alive). Protocol negotiation: server speaks 6, client clamps to 5.
- **`feature/rgb-openrgb-backend` @ 50499f2 + c498398**: full backend —
  `rgb/drivers/openrgb/{protocol,client,server,driver}.py`, CompositeDriver
  (prefix routing openrgb:N / aula_f75:0; exclude_vids 0x258A wired in
  app.py when AULA probe is healthy), settings rgb_openrgb_enabled/_port/
  _path, worker detach_requested queued shutdown, backend bundled under
  pulse_hwm/assets/openrgb (47.6MB incl. GPL LICENSE + SOURCE.txt), spec
  datas wired, THIRD_PARTY.md attributed, version bumped to 1.4.0 with
  CHANGELOG. Full pytest green, hooks pass, exe rebuilt (262.9MB onedir,
  backend lands at _internal\pulse_hwm\assets\openrgb\OpenRGB.exe).

### Next steps (in order)
1. **Gate B — owner hardware test of `dist\PulseHWM\PulseHWM.exe`** (v1.4.0):
   RGB tab → enable driver/mode → colors appear on MSI board zones + Kraken
   headset + G102 mouse; AULA keyboard still drives natively; closing the
   app stops the backed OpenRGB server we spawned.
2. On Gate B pass: owner decides merge to main + tag v1.4.0 push (CI builds
   the installer; GH release body = CHANGELOG [1.4.0]).
3. Untracked leftovers in the working tree are agent tooling (.agents/,
  .claude/, .opencode/, .wrangler/, node_modules, .strixignore) — not staged
  on any branch; .strixignore stays for the parked strix session.
4. Parked: Strix scan (once an LLM credit exists — see strix section below).



### v1.3.0 shipped TODAY (context, not pending work)
- `main` fast-forwarded to 1c4f66c and **tag v1.3.0 pushed**; CI built and
  published the PUBLIC GitHub release: PulseHWM-Setup-1.3.0.exe + SHA256SUMS
  + manifest-1.3.0.json(.sig). (Earlier: release page was empty, owner said
  "no way to download" — solved above.)
- Worker publish step "failed" misleadingly: crash was a cp1252
  UnicodeEncodeError on a success `print` containing a `→` char AFTER the
  POST had already succeeded (verified: the print sat inside the 2xx
  branch). Fixed in commit 20ce8d4 on main (publish_release.py now
  reconfigures stdout/stderr to utf-8/replace AND uses ASCII arrows).
- R2 upload step SKIPPED (var R2_ENABLED not set) — assets ship on GitHub
  for now, per docs/UPDATES.md runbook.
- `main` @ 20ce8d4 contains ONLY the release-train work + the cp1252 fix:
  the vendor-free P1 commits (raw HID / G102 lightsync, 95d77f3) were
  deliberately EXCLUDED from the release — they live on
  `feature/rgb-driver-kraken` (checked out here), 5 commits ahead of the tag.

---

# ARCHIVED — RGB engine handoff (2026-09-16, kept for context)

## Where we are (v1.3.0 release train, code-complete, not yet shipped)

- **24 phases landed across 20+ small branches** — each one stacked, rebased
  pile that built up at HEAD of `release/v1.3.0`. All commits prefixed
  `feat(rgb):` / `feat(settings):` / `chore(rgb):` / `release:`. Full pytest
  suite green, black/ruff/pre-commit hooks passing on every commit.
- The whole v1.3.0 work sits on branch `release/v1.3.0` (single chain).
- **NOT yet done:** merge to `main`, tag `v1.3.0`, push → CI builds the
  installer → GitHub release page (body = the `[1.3.0]` CHANGELOG section).
  This is gated on the OWNER testing `dist\PulseHWM\PulseHWM.exe` first.

## What shipped this session (in order of landing)

1. `pulse_hwm/rgb/` — Qt-free RGB subsystem:
   - `model.py` (RgbColor/RgbEffect/RgbDevice/LedLayout), `layout.py`
     (AULA F75 64-key map, grid, strip builders)
   - `effects/base.py` Effect ABC + EffectContext + ParamSpec;
     `builtin.py` STATIC/BREATHE/REACTIVE_TEMP (gradient)/REACTIVE_ALERT
     (4 Hz strobe); `declarative.py` JSON layer-stack interpreter
     (solid/gradient/wave/sparkle/reactive_temp/reactive_alert, params
     schema parsed to ParamSpec, unknown ops DROPPED at load — never executed);
     `loader.py` validate_definition (20KB/40-layer/8-param caps,
     `user_` prefix), UserEffectStore over `rgb_user_effects` blob,
     URLFetcher (HTTPS-only, streamed 20KB cap, gated by
     `rgb_allow_effect_urls` default OFF)
   - `drivers/`: base.py RgbDriver ABC (probe never raises, open/set_frame
     may), registry.py with broken-driver isolation, FakeDriver; real
     drivers: `aula_f75.py` (SinoWealth direct mode, hardware-verified),
     `logitech_g.py` (ctypes LogiLed, verified LIVE via LGS),
     `razer_chroma.py` (Chroma REST localhost:54235, offline-tested),
     `corsair_icue.py` (cuesdk surface, offline-tested),
     `msi_mystic.py` (ctypes MysticLight SDK, 16-slot palette-nearest),
     `asus_aura.py` (detection-only stub, ABC-enforced)
   - `engine.py` pure frame producer, `worker.py` RgbWorker QObject on
     "rgb-engine" QThread (queued signals: attach_requested,
     brightness_requested, sensors_requested, apply_assignments),
     `manager.py` (Qt-free RgbManager + pure ModePlanner: mode enum
     off/effects/reactive/override + AlertClock + maintain() expiry sweep)
   - `assignment_store.py` (pure blob CRUD), `layout.py`, `aula_protocol.py`
     (OEM effect protocol codec — kept pure, NOT the shipped transport)
2. `app_settings.py`: all `rgb_*` keys (device-local, never sync);
   load-time validation degrades corrupt enum/hex/JSON values to defaults.
3. UI: `ui/rgb_tab.py` (status/mode/override/dev+assignments/effects import
   panels + color picker `ui/widgets/color_picker.py` and color_math.py),
   `main_window.py` RGB tab wired, `app.py` rgb thread (built before
   window) + sensor push (collector.updated → cpu/gpu/mem/max_temp) +
   alert dispatch listener (`add_dispatch_listener` in notifier.py, error
   level only) + 500ms maintain job + shutdown.
4. M6 settings rework: `ui/widgets/settings_widgets.py` (FormRow,
   CollapsiblePanel, SettingsRail), `settings_tab.py` rebuilt as left rail
   (2 pages), `theme.qss` #railButton tokens.
5. Release prep: `__init__.py` → 1.3.0, CHANGELOG `[1.3.0]` section,
   THIRD_PARTY.md vendor/protocol attributions, requirements dedup'd.
6. Final exe: `dist\PulseHWM\PulseHWM.exe` (onedir). PyInstaller spec has
   `hid` + `httpx` hiddenimports.

## Hardware verification status (owner confirmed live)

- AULA F75 (wired 258A:010C): direct-mode RED→OFF→GREEN flash ✓,
  engine-stack violet breathe ✅, reactive temp sweep green→red ✅.
- Logitech: lived via LGS install — RED then GREEN confirmed via
  LogitechLED SDK.
- **Owner-reported issue:** mid-gradient colors (yellow/orange on the F75)
  render washed-out/near-white — LED color-mixing artifact. Recorded in
  docs/RGB.md; compact output palette is a tracked follow-up. NOT fixed.
- Owner tested `--selftest` once only via the standalone replay; the
  `--rgb-selftest` integration probe inside the exe is UNVERIFIED so far.

## Answers/decisions already made (do not re-ask)

- Own engine (NO OpenRGB); vendor SDKs are transport-only per ecosystem.
- Single mode enum `rgb_mode` = off|effects|reactive|override (no booleans).
- Built-in effects in Python; imported effects are declarative JSON only
  (arbitrary Python import = RCE, never shipped); URL fetch allowed but
  opt-in; `rgb_allow_external_plugins` (drop-in drivers) default OFF, not
  yet implemented in registry (only shaped for).
- Per-LED frames + layouts in the SAME release; render rate is a setting.
- RGB knobs stay device-local (`SYNCABLE_KEYS` excludes all `rgb_*`).
- Settings rework (M6) done concurrently; RGB got its own top-level tab.

## OPEN QUESTIONS the owner asked — answer these first

1. "Why are the vendor drivers fakes?" — ANSWER given: fake-injected SDKs
   exist for CI-only tests; the drivers themselves are real code paths.
   Logitech verified LIVE (LGS installed by me). Owner accepted offset, but
   they then asked:
2. "Can we drive RGB WITHOUT installing vendor software (iCUE/MSI Center =
   bloatware)?" — Decision: YES but parked. New final phase (vendor-free
   OpenRGB-style raw HID drivers per device family; MSI motherboard needs
   kernel driver — flag the risk). Logitech currently runs through LGS.
   iCUE install was ABORTED mid-run by the owner — check
   `winget list Corsair.iCUE.5` to see if it half-landed before proceeding
   to anything Corsair-related.
3. Owner wants release page updated with **v1.3.0** when work is done —
   gated on their exe smoke test.
4. **I MUST uninstall Logitech LGS (winget Logitech.LGS) before starting the
   vendor-free driver phase** — recorded in docs/RGB.md; confirm with owner
   whether to do it now or at that phase boundary.

## Immediate next steps (in order)

1. Owner smokes `dist\PulseHWM\PulseHWM.exe` (boots to v1.3.0, Settings
   rail, RGB tab override colors the F75).
2. On approval: merge `release/v1.3.0` → `main` (fast-forward preferred,
   PR optional per AGENTS.md), tag `v1.3.0`, push; CI builds
   `PulseHWM-Setup-1.3.0.exe`; create GitHub release whose body = the
   CHANGELOG `[1.3.0]` section; worker notifies registered installs.
3. THEN the parked vendor-free RGB driver phase (owner-approved direction),
   INCLUDING: uninstall LGS first, per-driver USB captures from the owner's
   hardware, F75 per-key index calibration, compact F75 palette fix,
   dongle PID 010D support.
4. If anything regresses: regression risk spots =
   `rgb_engine` thread detach on shutdown, `razer_chroma` REST heartbeat,
   `msi_mystic` palette-nearest, corsair broadcast_colors (needs rewire to
   real cuesdk API once iCUE is truly installed — driver currently calls a
   fake-shaped `broadcast_colors`).

## Session-specific traps (context-rot killers)

- **ctypes quirk in this venv:** `int(ctypes.c_int(x))` raises
  `invalid literal for int() with base 10: b'...'` — NEVER wrap ctypes args
  in c_int for ctypes/python-object calls; pass plain ints and set
  `.argtypes`. Logitech + MSI drivers already do this.
- **Write tool placeholder-tail failure mode:** I kept emitting corrupted
  function tails when writing long files in one shot (grep for `# ???` /
  `placeholder` / `del self.py`). New files went through a "complete clean
  rewrite after emitting junk" loop — keep doing that: WRITE, then IMMEDIATELY
  grep `Placeholder|TODO|.` suspicious tokens before test/commit.
- pre-commit stash/restore conflicts leave unstaged churn — if a hook "fails
  with conflict", stages are clean; re-run `git add` + `pre-commit run`.
- PowerShell: no heredocs (`<<`) and no bash pipelines; use
  temp .py files under `%TEMP%\opencode\` or `Select-String`.
- exe lock: `Get-Process PulseHWM` before `pyinstaller pulse_hwm.spec
  --noconfirm`; the launcher is at `dist\PulseHWM\PulseHWM.exe` (onedir).
- `.env` is gitignored and must never be staged; secret scanners fail the
  commit (gitleaks + custom scan).
- The DB is `settings(key,value)` (key-value TEXT); `settings_sync` mirrors
  LWW; `settings_collapsed::<section>` keys and `rgb_*` keys persist there.
  `rgb_device_assignment` blob keys are `"<driver_id>/<device_id>"`.

## Branch/PR conventions in play

- Stacked small branches, each = 1 phase; PRs opened when the owner asks
  (they asked for stacked PRs). CodeRabbit reviews each PR's LAYER diff
  independently (base = branch below), not the whole stack.
- Rebuild the exe at milestone boundaries (closing PulseHWM first if running).
- `docs/RGB.md` is the running engineering log — append per phase.
