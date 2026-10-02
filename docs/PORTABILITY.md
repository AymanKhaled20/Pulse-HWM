# macOS / Linux portability audit

Audited 2026-10-01 on `feature/rgb-tab-rework` (v1.4.0 code). Plan: ship a
stable Windows **v1.5.0** first, then port, **Linux before macOS**.

Effort: **S** = hours, **M** = a day or two, **L** = a real project.

## Already portable

Qt UI, tray icon, single instance (`QLocalServer`), bundled fonts, SQLite,
psutil, NVML GPU stats, battery, website checks, cloud sync / REST client,
Ed25519 update-manifest verification. `pywin32`, `wmi` and `winotify` already
carry `sys_platform == "win32"` markers in `requirements.txt`.

Update trust does not depend on Windows: `AUTHENTICODE_REQUIRED = False` in
`cloud/updates/trust.py`, so the Ed25519 signature is the anchor everywhere.

## Windows-only code

| Area | Where | Windows today | Linux | macOS | Effort |
|---|---|---|---|---|---|
| Data / log folder | `config.data_dir()` | `%LOCALAPPDATA%`; without it data falls back to `REPO_ROOT/data`, i.e. **inside the install folder** in a frozen build | `~/.local/share/PulseHWM` (XDG) | `~/Library/Application Support/PulseHWM` | S |
| Self-test | `app._selftest()` | unguarded `ctypes.windll` → **crashes** off Windows | `os.geteuid() == 0` | same | S |
| Installer mutex | `app._create_app_mutex` | `win32event` (already fails soft) | not needed | not needed | none |
| CPU name | `util.short_cpu_name` | registry (already falls back) | `/proc/cpuinfo` | `sysctl -n machdep.cpu.brand_string` | S |
| Restart as admin | `util.shell_runas`, `util.is_admin`, settings tab | `ShellExecute runas` (UAC) | `pkexec`, or hide the button | hide the button | S |
| Alert sound | `alerts/notifier._win_sound` | `winsound` | `QSoundEffect` with the same synthesized WAV | same | S |
| Toast | `alerts/notifier.send_toast` | `winotify` (falls back to the tray balloon) | tray works; `notify-send` is nicer | tray works | S |
| Temperatures | `collectors/lhm.py`, `hardware._temps_*` | LibreHardwareMonitorLib via pythonnet, WMI | `psutil.sensors_temperatures()` (hwmon) | **hard**: Apple Silicon needs private IOKit/HID APIs or `powermetrics` (sudo); Intel needs SMC | Linux S, mac L |
| Processes tab | `processes.py` | `%SystemRoot%` classification, `win32gui` visible windows, `win32pdh` private working set, `EmptyWorkingSet` | system = uid / known paths; windows via X11 (Wayland can't enumerate); RSS/USS memory | system = uid + `/System`; windows via `CGWindowListCopyWindowInfo` | M each |
| `pulsehwm://` sign-in links | `cloud/oauth.py`, `single_instance.py`, installer `[Registry]` | HKCU URL protocol, URL arrives in argv | `.desktop` file with `MimeType=x-scheme-handler/pulsehwm`; argv handoff unchanged | **different path**: URL arrives as a `QFileOpenEvent`, scheme declared in `Info.plist` (`CFBundleURLTypes`) | Linux S, mac M |
| Tokens | `cloud/token_store.py` | Credential Manager via `keyring` | Secret Service (GNOME Keyring / KWallet); minimal desktops may lack it → clear "unavailable" message | Keychain via `keyring` | S |
| RGB server | `rgb/drivers/openrgb/server.py` | finds `OpenRGB.exe` only, `CREATE_NO_WINDOW` | `openrgb` binary or AppImage, plus **udev rules** for HID access and `i2c-dev` for motherboard/RAM | OpenRGB support is limited: many USB devices, no SMBus (motherboard/RAM) | Linux M, mac M (limited) |
| Update install | `cloud/updates/installer.py`, `policy.py`, worker `updates.js` / `download.js` | spawns `PulseHWM-Setup-x.exe /SILENT`; worker **only accepts** `PulseHWM-Setup-*.exe` | AppImage (in-place swap) or `.deb`/`.rpm` (show the release instead of auto-installing) | `.dmg` / `.app` swap; must be signed + notarized (Apple Developer Program, $99/yr) or Gatekeeper blocks it | **L**: worker schema, manifest naming and publish step all go per-platform |
| Build / installer | `pulse_hwm.spec`, `installer/*.iss`, `release.yml` | `\` paths in the spec, Inno Setup, CI on `windows-latest` only | per-platform spec + AppImage step + `ubuntu-latest` job | `.app` bundle + `.dmg` + signing + `macos-latest` job | M each |
| Dependencies | `requirements.txt` | `pythonnet` has no platform marker (installs elsewhere but needs Mono/.NET) | add `; sys_platform == "win32"` | same | S |

## Takeaways

- **Linux**: roughly 2–3 weeks part-time. Temps and tokens are nearly free; the
  real work is the processes tab, RGB device permissions, and the
  update/release pipeline.
- **macOS**: clearly more. Temps, signing/notarization and the URL scheme are
  each their own project, and it needs a Mac to build and test on.
- **Groundwork doable now on Windows, no behavior change**: fix the data/log
  folder fallback, the self-test crash and the `pythonnet` marker, then put
  toast, sound, CPU name, admin and temperature sources behind one
  `platform/` module each. Porting then means adding implementations rather
  than editing call sites.
