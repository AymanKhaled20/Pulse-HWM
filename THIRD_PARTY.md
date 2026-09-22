# Third-party software

## RGB control systems

### SinoWealth / AULA F75 protocol research

Pulse-HWM's native keyboard driver implements the SinoWealth (BY Tech) HID
protocol — reverse-engineered by the community — under the following
credit:

- **marcoslor / Aula-F87-Controller** (`docs/PROTOCOL.md`): fragment format,
  checksum, 4-phase effect sequence, per-key planar layout, apply-flag
  quirk. Protocol observed via USB captures; same firmware family on the
  AULA F75 (VID 0x258A, PID 0x010C).
- **OpenRGB / CalcProgrammer1 — SinowealthKeyboard10cController**
  (GPL-2.0-or-later): the 520-byte direct-mode feature-report frame shape
  (`0x06` header + RGB triplet stream) our driver transmits, plus the
  keepalive/probing behavior (usage-page discovery quirk).
  https://gitlab.com/CalcProgrammer1/OpenRGB
- **vndarkblue / aula-keybind**, **Ghost-CR / F75_Initializer**,
  **veysiemrah / aula-rgb-controller**: supporting community reverse
  engineering of the same chip family.

No source of these projects is embedded; Pulse implements an independent
Python implementation of the OVER-THE-WIRE protocol. Direct-mode is a data
serialization format (constants + LED triplets) — attribution is given in
the spirit of the research community.

### Manufacturer-independent RGB backend (planned: OpenRGB fork)

The vendor-SDK and raw-HID drivers were removed; RGB devices beyond the AULA
F75 are handled by a headless OpenRGB fork spawned as a separate process and
driven over its documented SDK protocol (loopback). See `docs/RGB.md`.
OpenRGB attribution (GPL-2.0-or-later, source at the fork) will be detailed
here when the backend feature lands.

## LibreHardwareMonitorLib

Pulse-HWM embeds **LibreHardwareMonitorLib** (`pulse_hwm/assets/lhm_runtime/`,
fetched on first run) to read CPU, GPU, motherboard, and drive temperature
sensors directly in-process.

- Project: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- Version: 0.9.6
- License: **Mozilla Public License 2.0 (MPL-2.0)** — not MIT.
  Full text: `pulse_hwm/assets/lhm/MPL-2.0.txt`
- Use of the unmodified library binary does not make Pulse-HWM MPL-licensed;
  only changes to LibreHardwareMonitor's own source files would carry the
  MPL obligation. We vendor the official release binaries unmodified.
- Source is available for the vendored version at:
  https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/tag/v0.9.6

Huge credit to the LibreHardwareMonitor team and contributors — this app's
temperature sensor readout would not be possible without them.

## Other runtime dependencies

| Package | License |
|---|---|
| PySide6 | LGPL-3.0 |
| pyqtgraph | MIT |
| psutil | BSD-3 |
| httpx | BSD-3 |
| python-dotenv | BSD-3 |
| pythonnet | MIT |
| nvidia-ml-py | MIT |
| wmi | MIT |
| winotify | MIT |
| PyInstaller | GPL with bootloader exception |

Fonts (bundle in `pulse_hwm/assets/fonts/`, licenses next to each family in
`pulse_hwm/assets/fonts/licenses/`):

- **SIL OFL-1.1**: Silkscreen, VT323, Press Start 2P (bundled from the start)
  plus Anonymous Pro, Audiowide, Chakra Petch (OFL), Codystar, DM Mono,
  DotGothic16, Fira Code, Handjet, IBM Plex Mono, Jersey 10, JetBrains Mono,
  Orbitron, Pixelify Sans, Share Tech Mono, Space Mono, Tiny5 (v0.1.2).

All bundled font files are unmodified; OFL permits bundling and requires that
the license text accompany the fonts, which it does in `assets/fonts/licenses/`.
