"""OpenRgbDriver — the RgbDriver contract over the OpenRGB backend.

Mapping decisions:
  * device_id is "openrgb:<index>" where index is the server-side device
    index (protocol 5, indexed scheme). Frames route by this id.
  * open() starts/attaches the headless server, reads the controller list,
    and switches EVERY device to custom (direct) mode — OpenRGB hardware
    modes would fight our own frame producer otherwise.
  * Controllers whose device VID is in exclude_vids are not enumerated;
    app.py puts 0x258A (AULA/Sinowealth) there while the native driver
    owns the keyboard, so the two never fight for it.
"""

from __future__ import annotations

import socket

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.drivers.openrgb import protocol as P
from pulse_hwm.rgb.drivers.openrgb.client import OrgbClient
from pulse_hwm.rgb.drivers.openrgb.server import (
    OrgbServer,
    OrgbServerNotFound,
)
from pulse_hwm.rgb.model import LedLayout, RgbColor, RgbDevice


def vid_from_location(location: str) -> int | None:
    """Parse the VID from an OpenRGB HID-style location string such as
    'HID: /?HID#VID_258A&PID_010C&...'. Non-HID locations (e.g. SMBus)
    have no VID and return None."""
    marker = location.find("VID_")
    if marker < 0:
        return None
    hexpart = location[marker + 4 : marker + 8]
    try:
        return int(hexpart, 16)
    except ValueError:  # 4 chars after VID_ are not a hex VID
        return None


def frame_from_colors(
    device: RgbDevice, colors: list[RgbColor]
) -> list[tuple[int, int, int]]:
    """Pad-or-truncate a per-LED frame to the device's LED count."""
    count = max(1, device.leds)
    filled = [(c.r, c.g, c.b) for c in colors[:count]]
    if filled and len(filled) < count:
        filled += [filled[-1]] * (count - len(filled))
    return filled


class OpenRgbDriver(RgbDriver):
    driver_id = "openrgb"
    name = "OpenRGB backend"
    version = "1"
    requires_app = ""  # bundled in our installer; nothing for the user to install

    def __init__(self, port: int = P.DEFAULT_PORT) -> None:
        self._port = port
        self._server: OrgbServer | None = None
        self._client: OrgbClient | None = None
        self._devices: list[RgbDevice] = []
        self._spawned = False  # True iff WE started the server process
        self.exclude_vids: frozenset[int] = frozenset()

    # ── RgbDriver contract ────────────────────────────────────────────────

    def probe(self) -> ProbeResult:
        """Side-effect free: availability = port already open OR bundled
        binary present (spawning is left to open(), never done here)."""
        if _port_open(self._port):
            return ProbeResult(True)
        try:
            _find_bundled()
        except OrgbServerNotFound:
            return ProbeResult(
                False,
                reason="OpenRGB backend not found (openrgb.exe missing in assets)",
            )
        return ProbeResult(True)

    def open(self) -> None:
        """Connect-or-spawn, enumerate, put every device into custom mode.
        May raise (missing binary / server never starts); callers degrade."""
        server = OrgbServer(port=self._port)
        spawned_via = server.ensure_running()
        self._spawned = bool(spawned_via)
        self._server = server
        client = OrgbClient(port=self._port)
        client.connect()
        for controller in client.controllers:
            client.set_custom_mode(controller.index)
        self._client = client

    def close(self) -> None:
        """Best-effort release — never raises (matches driver contract)."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        if self._server is not None and self._spawned:
            self._server.stop()
        self._devices = []

    def devices(self) -> list[RgbDevice]:
        client = self._client
        if client is None:
            return []
        result: list[RgbDevice] = []
        for controller in client.controllers:
            if vid_from_location(controller.location) in self.exclude_vids:
                continue
            result.append(
                RgbDevice(
                    device_id=f"openrgb:{controller.index}",
                    name=controller.name or controller.vendor or "OpenRGB device",
                    driver_id=self.driver_id,
                    leds=controller.led_count or 1,
                    layout=self._layout(controller),
                )
            )
        self._devices = result
        return result

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        client = self._client
        if client is None or not device_id.startswith("openrgb:"):
            return False
        try:
            index = int(device_id.split(":", 1)[1])
        except ValueError:
            return False
        device = next((d for d in self._devices if d.device_id == device_id), None)
        if device is None:
            # server-side list may have changed since enumerate; refresh
            self._devices = self.devices()
            device = next((d for d in self._devices if d.device_id == device_id), None)
            if device is None:
                return False
        try:
            client.update_leds(index, frame_from_colors(device, colors))
        except Exception:  # races degrade silently at render rate
            return False
        return True

    # ── layout ────────────────────────────────────────────────────────────

    def _layout(self, controller: P.OrgbController) -> LedLayout | None:
        """Minimal layout: zone → evenly-spread strip positions. Matrix maps
        are dropped this release; tracked in docs/RGB.md. Single-zone whole
        devices get a trivial layout and let the engine place colors."""
        if not controller.zones:
            return LedLayout(led_count=controller.led_count or 1)
        positions: list[tuple[float, float]] = []
        for zone in controller.zones:
            n = zone.leds_count or 1
            positions.extend((i / n, 0.0) for i in range(n))
        return LedLayout(led_count=len(positions) or 1, positions=tuple(positions))


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _find_bundled() -> str:
    from pulse_hwm.rgb.drivers.openrgb.server import _find_bundled

    return _find_bundled()
