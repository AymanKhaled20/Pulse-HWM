"""OpenRgbDriver helpers + probe path.

No real server, no real spawning: the driver tests exercise pure helpers
and the degraded paths (no client → clean fail-returns). The spawn path is
covered by the Gate A hardware run recorded in docs/RGB.md.
"""

from __future__ import annotations

from dataclasses import replace

from pulse_hwm.rgb.drivers.openrgb import driver as driver_module
from pulse_hwm.rgb.drivers.openrgb import protocol as P
from pulse_hwm.rgb.drivers.openrgb.driver import (
    OpenRgbDriver,
    _is_resizable_argb_zone,
    frame_from_colors,
)
from pulse_hwm.rgb.model import RgbColor, RgbDevice


def test_dynamic_argb_zone_names_are_resizable() -> None:
    assert _is_resizable_argb_zone("JRAINBOW1") is True
    assert _is_resizable_argb_zone("JARGB2") is True
    assert _is_resizable_argb_zone("Mouse Zone") is False


def test_frame_from_colors_pads_and_truncates() -> None:
    device = RgbDevice(device_id="openrgb:0", name="m", driver_id="openrgb", leds=4)
    one = frame_from_colors(device, [RgbColor(9, 8, 7)])
    assert one == [(9, 8, 7)] * 4
    trunc = frame_from_colors(device, [RgbColor(1, 1, 1)] * 6)
    assert len(trunc) == 4
    assert trunc[-1] == (1, 1, 1)


def test_driver_unavailable_without_binary_or_port() -> None:
    driver = OpenRgbDriver(port=1)  # port 1 closed in tests; binary likely present
    result = driver.probe()
    if not result.available:
        assert "not found" in result.reason
    else:
        assert result.available


# ── fakes for the driver's server/client (no sockets, no processes) ──────


def _controller(index: int, name: str, leds: int = 3) -> P.OrgbController:
    return P.OrgbController(
        index=index,
        device_type=P.DEVICE_TYPE_MOUSE,
        name=name,
        vendor="ACME",
        description="",
        version="",
        serial="",
        location="",
        led_count=leds,
        active_mode=0,
        mode_names=("Direct",),
    )


class FakeClient:
    """Stands in for OrgbClient. `applies` decides whether the pretend
    server stores the frames we send (the bug: it silently did not)."""

    def __init__(self, controllers: list[P.OrgbController], applies: bool = True):
        self.controllers = controllers
        self.applies = applies
        self.stored: dict[int, tuple[int, ...]] = {}
        self.custom_mode_calls: list[int] = []
        self.rescans = 0
        self.refreshes: list[float | None] = []
        self.notifications: set[int] = set()

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def rescan(self) -> None:
        self.rescans += 1

    def refresh(self, settle_s: float | None = None) -> None:
        self.refreshes.append(settle_s)

    def refresh_current_controller_data(self) -> None:
        pass

    def resize_zone(self, index: int, zone_index: int, led_count: int) -> None:
        pass

    def set_custom_mode(self, index: int) -> None:
        self.custom_mode_calls.append(index)

    def update_leds(self, index: int, colors: list[tuple[int, int, int]]) -> None:
        if self.applies:
            self.stored[index] = tuple(P.pack_color(*rgb) for rgb in colors)

    def read_controller(self, index: int) -> P.OrgbController:
        base = next(c for c in self.controllers if c.index == index)
        return replace(base, colors=self.stored.get(index, (0,) * base.led_count))

    def poll_notifications(self) -> set[int]:
        seen, self.notifications = self.notifications, set()
        return seen


class FakeServer:
    def __init__(self, port: int) -> None:
        pass

    def ensure_running(self) -> str:
        return ""  # "already running": the driver must not stop it on close

    def stop(self) -> None:
        pass


def _open_driver(monkeypatch, client: FakeClient) -> OpenRgbDriver:
    monkeypatch.setattr(driver_module, "OrgbServer", FakeServer)
    monkeypatch.setattr(driver_module, "OrgbClient", lambda port: client)
    driver = OpenRgbDriver(port=1)
    driver.open()
    driver.devices()
    return driver


def _send(driver: OpenRgbDriver, device_id: str, frames: int) -> list[bool]:
    red = [RgbColor(255, 0, 0)] * 3
    return [driver.set_frame(device_id, red) for _ in range(frames)]


def test_open_never_rescans_and_sets_every_device_to_direct(monkeypatch) -> None:
    """A rescan renumbered OpenRGB's controllers, after which (up to fork
    13234cb) every frame from us was silently dropped."""
    client = FakeClient([_controller(0, "Mouse"), _controller(1, "Keyboard")])
    _open_driver(monkeypatch, client)
    assert client.rescans == 0
    assert client.custom_mode_calls == [0, 1]


def test_frames_that_are_applied_verify_ok(monkeypatch) -> None:
    driver = _open_driver(monkeypatch, FakeClient([_controller(0, "Mouse")]))
    results = _send(driver, "openrgb:0", OpenRgbDriver.VERIFY_AFTER_FRAMES + 3)
    assert all(results)
    assert driver._verify_state["openrgb:0"] is True


def test_frames_that_are_never_applied_are_reported(monkeypatch) -> None:
    """The original bug: every frame 'accepted', none shown. The driver must
    now say so (engine -> RGB tab error strip) instead of claiming success."""
    client = FakeClient([_controller(0, "Mouse")], applies=False)
    driver = _open_driver(monkeypatch, client)
    frames = OpenRgbDriver.VERIFY_AFTER_FRAMES + OpenRgbDriver.VERIFY_MAX_MISSES + 2
    results = _send(driver, "openrgb:0", frames)
    assert results[0] is True  # undecided at first
    assert results[-1] is False
    assert "does not apply" in driver.last_error


def test_device_list_change_rereads_devices_and_resets_checks(monkeypatch) -> None:
    client = FakeClient([_controller(0, "Mouse")])
    driver = _open_driver(monkeypatch, client)
    _send(driver, "openrgb:0", OpenRgbDriver.VERIFY_AFTER_FRAMES + 2)
    assert driver.poll_changes() is False  # nothing announced yet

    # replug: the keyboard came back as a NEW controller
    client.controllers = [_controller(0, "Mouse"), _controller(1, "Keyboard")]
    client.notifications = {P.PKT_DEVICE_LIST_UPDATED}
    client.custom_mode_calls.clear()
    assert driver.poll_changes() is True
    assert client.refreshes == [OpenRgbDriver.REDETECT_SETTLE_S]
    assert client.custom_mode_calls == [0, 1]  # both back in direct mode
    assert driver._verify_state == {}  # re-verify the new numbering
    assert [d.device_id for d in driver.devices()] == ["openrgb:0", "openrgb:1"]


def test_rescan_finds_a_device_missed_at_startup(monkeypatch) -> None:
    """OpenRGB's startup detection missed the keyboard; a rescan must ask
    the server to detect again, re-read devices and switch them to direct."""
    client = FakeClient([_controller(0, "Mouse")])
    driver = _open_driver(monkeypatch, client)
    _send(driver, "openrgb:0", OpenRgbDriver.VERIFY_AFTER_FRAMES + 2)

    client.controllers = [_controller(0, "Mouse"), _controller(1, "AULA F75")]
    client.notifications = {P.PKT_DEVICE_LIST_UPDATED}  # the rescan's own notice
    client.custom_mode_calls.clear()
    assert driver.rescan() is True
    assert client.rescans == 1
    assert client.refreshes == [OpenRgbDriver.RESCAN_SETTLE_S]
    assert client.custom_mode_calls == [0, 1]
    assert driver._verify_state == {}
    assert [d.name for d in driver.devices()] == ["Mouse", "AULA F75"]
    assert driver.poll_changes() is False  # notice consumed, no double re-read


def test_rescan_failure_is_reported_not_raised(monkeypatch) -> None:
    client = FakeClient([_controller(0, "Mouse")])
    driver = _open_driver(monkeypatch, client)

    def broken_rescan() -> None:
        raise OSError("server went away")

    client.rescan = broken_rescan
    assert driver.rescan() is False


def test_rescan_without_a_connection_does_nothing() -> None:
    assert OpenRgbDriver(port=1).rescan() is False
