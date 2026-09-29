"""OpenRgbDriver — the RgbDriver contract over the OpenRGB backend.

Mapping decisions:
  * device_id is "openrgb:<index>" where index is the server-side device
    index (protocol 5, indexed scheme). Frames route by this id.
  * open() starts/attaches the headless server, reads the controller list,
    and switches EVERY device to custom (direct) mode — OpenRGB hardware
    modes would fight our own frame producer otherwise.
  * OpenRGB is the ONLY transport: every device, including the AULA F75
    keyboard, is enumerated and driven through it.
  * Frames get no reply, so "sent" does not prove "applied". A few frames
    after (re)attach each device's stored colors are read back once; a
    device whose colors never match is reported through last_error
    instead of failing silently. (That is how the OpenRGB server bug where
    every frame was dropped after a rescan went unnoticed.)
  * OpenRGB announces unplug/replug with DEVICE_LIST_UPDATED; poll_changes()
    re-reads the list and puts the devices back into direct mode, because
    re-detected devices come back in their hardware mode.
"""

from __future__ import annotations

import logging
import socket

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.drivers.openrgb import protocol as P
from pulse_hwm.rgb.drivers.openrgb.client import OrgbClient
from pulse_hwm.rgb.drivers.openrgb.server import (
    OrgbServer,
    OrgbServerNotFound,
)
from pulse_hwm.rgb.model import LedLayout, RgbColor, RgbDevice

log = logging.getLogger("pulse.rgb")

# modes in which OpenRGB shows the per-LED colors we send (SetCustomMode
# picks the first of these the device offers)
_PER_LED_MODES = frozenset({"Direct", "Custom", "Static"})


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
    DEFAULT_RESIZABLE_ZONE_LEDS = 60
    # after a replug the count only has to look stable briefly (the cold
    # start needs the client's full window while detection runs)
    REDETECT_SETTLE_S = 2.0
    # read-back check: skip the first frames (the server applies them on a
    # background thread), then allow a few misses before calling it broken
    VERIFY_AFTER_FRAMES = 5
    VERIFY_MAX_MISSES = 5

    def __init__(self, port: int = P.DEFAULT_PORT) -> None:
        self._port = port
        self._server: OrgbServer | None = None
        self._client: OrgbClient | None = None
        self._devices: list[RgbDevice] = []
        self._spawned = False  # True iff WE started the server process
        self.last_error = ""  # read by the engine when set_frame returns False
        self._reset_verification()

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
        # connect() waits until the controller count has been stable for a
        # while, which already covers the late AULA/Sinowealth detection.
        # NO rescan here: a rescan renumbers the server's controllers, and
        # OpenRGB builds up to 13234cb then silently dropped every frame
        # from protocol-5 clients (like us). A later replug is handled by
        # poll_changes() instead.
        client.connect()
        self._prepare_controllers(client)
        self._client = client
        self._reset_verification()

    def _prepare_controllers(self, client: OrgbClient) -> None:
        """Size the ARGB headers and switch every device to direct mode.
        Used at open() and again after the device list changed."""
        for controller in client.controllers:
            for zone_index, zone in enumerate(controller.zones):
                if zone.leds_count == 0 and _is_resizable_argb_zone(zone.name):
                    client.resize_zone(
                        controller.index,
                        zone_index,
                        self.DEFAULT_RESIZABLE_ZONE_LEDS,
                    )
        for controller in client.controllers:
            client.set_custom_mode(controller.index)
        # re-read the same indexes: picks up resized LED counts AND lets us
        # check the mode switch really happened
        client.refresh_current_controller_data()
        for controller in client.controllers:
            if controller.active_mode_name not in _PER_LED_MODES:
                log.warning(
                    "%s stayed in mode %r after the switch to direct mode; "
                    "its LEDs may not show Pulse's colors",
                    controller.name,
                    controller.active_mode_name,
                )

    def poll_changes(self) -> bool:
        """Re-read devices after OpenRGB announced a device-list change."""
        client = self._client
        if client is None:
            return False
        try:
            seen = client.poll_notifications()
        except Exception as exc:
            log.warning("reading OpenRGB notifications failed: %s", exc)
            return False
        if P.PKT_DEVICE_LIST_UPDATED not in seen:
            return False
        log.info("OpenRGB device list changed (replug?); re-reading devices")
        try:
            client.refresh(settle_s=self.REDETECT_SETTLE_S)
            self._prepare_controllers(client)
        except Exception:
            log.exception("re-reading OpenRGB devices failed")
            return False
        self._reset_verification()
        return True

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
        self._reset_verification()

    def devices(self) -> list[RgbDevice]:
        client = self._client
        if client is None:
            return []
        result: list[RgbDevice] = []
        for controller in client.controllers:
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
        frame = frame_from_colors(device, colors)
        if self._verify_state.get(device_id) is None:
            self._check_applied(client, index, device_id)
        try:
            client.update_leds(index, frame)
        except Exception as exc:  # races degrade at render rate
            self.last_error = f"send failed: {exc}"
            return False
        self._last_sent[device_id] = tuple(P.pack_color(*rgb) for rgb in frame)
        self._frames_sent[device_id] = self._frames_sent.get(device_id, 0) + 1
        if self._verify_state.get(device_id) is False:
            self.last_error = (
                "OpenRGB accepts frames but does not apply them "
                "(outdated OpenRGB build?)"
            )
            return False
        return True

    # ── read-back check ──────────────────────────────────────────────────

    def _reset_verification(self) -> None:
        # device_id -> True (colors verified) / False (never applied);
        # missing = not decided yet
        self._verify_state: dict[str, bool] = {}
        self._verify_misses: dict[str, int] = {}
        self._last_sent: dict[str, tuple[int, ...]] = {}
        self._frames_sent: dict[str, int] = {}

    def _check_applied(self, client: OrgbClient, index: int, device_id: str) -> None:
        """Compare the server's stored colors with the PREVIOUS frame (the
        latest one may still be queued on the server's device thread)."""
        previous = self._last_sent.get(device_id)
        if previous is None or self._frames_sent.get(device_id, 0) < (
            self.VERIFY_AFTER_FRAMES
        ):
            return
        try:
            stored = client.read_controller(index).colors
        except Exception:
            return  # try again on the next frame
        if tuple(stored) == previous:
            self._verify_state[device_id] = True
            log.info("%s: OpenRGB applies Pulse's colors", device_id)
            return
        misses = self._verify_misses.get(device_id, 0) + 1
        self._verify_misses[device_id] = misses
        if misses >= self.VERIFY_MAX_MISSES:
            self._verify_state[device_id] = False
            log.error(
                "%s: OpenRGB accepted %d frames but its stored colors never "
                "matched them; the lights will not change",
                device_id,
                self._frames_sent.get(device_id, 0),
            )

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


def _is_resizable_argb_zone(name: str) -> bool:
    """Identify addressable-header zones reported at size zero."""
    normalized = str(name).strip().upper()
    return normalized.startswith(("JRAINBOW", "JARGB", "ARGB"))
