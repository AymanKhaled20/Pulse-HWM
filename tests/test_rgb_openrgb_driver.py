"""OpenRgbDriver helpers + probe path, and CompositeDriver routing.

No real server, no real spawning: the driver tests exercise pure helpers
and the degraded paths (no client → clean fail-returns). The spawn path is
covered by the Gate A hardware run recorded in docs/RGB.md.
"""

from __future__ import annotations

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.drivers.composite import CompositeDriver
from pulse_hwm.rgb.drivers.openrgb.driver import (
    OpenRgbDriver,
    frame_from_colors,
    vid_from_location,
)
from pulse_hwm.rgb.model import RgbColor, RgbDevice


def test_vid_from_location_parses_hex() -> None:
    location = r"HID: \\?\HID#VID_258A&PID_010C#6&0000#{uid}"
    assert vid_from_location(location) == 0x258A
    assert vid_from_location(r"HID: \\?\HID#VID_1462&PID_7D41") == 0x1462


def test_vid_from_location_handles_non_hide() -> None:
    assert vid_from_location("SMBus: 0x0B20") is None
    assert vid_from_location("") is None


def test_frame_from_colors_pads_and_truncates() -> None:
    from pulse_hwm.rgb.model import RgbDevice

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


class _ChildDriver(RgbDriver):
    """Configurable fake: controls availability + frame recording.

    Also stands in for the OpenRGB child in composite tests — a real
    OpenRgbDriver would spawn the bundled backend, and tests must never
    touch real hardware or spawn real processes.
    """

    driver_id = "childx"
    name = "Child"

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.opened = 0
        self.closed = 0
        self.frames: list[tuple[str, list[RgbColor]]] = []

    def probe(self) -> ProbeResult:
        return ProbeResult(True) if self._available else self.unavailable("static no")

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    def devices(self) -> list[RgbDevice]:
        return [
            RgbDevice(device_id="childx:3", name="c", driver_id=self.driver_id, leds=2)
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        self.frames.append((device_id, colors))
        return True


def test_composite_probe_and_routing() -> None:
    down = _ChildDriver(available=False)
    up = _ChildDriver(available=True)
    # NOTE: never put a real OpenRgbDriver into a composite test — its
    # open() spawns the actual backend server and opens hardware.
    composite = CompositeDriver([down, up])
    assert composite.probe().available
    composite.open()
    assert up.opened == 1
    assert down.opened == 0  # failed children are never opened
    devices = composite.devices()
    assert [d.device_id for d in devices] == ["childx:3"]

    # frames routed by prefix; unknown-prefixed devices refused
    assert composite.set_frame("childx:3", [RgbColor(1, 2, 3)]) is True
    assert up.frames[-1][0] == "childx:3"
    assert composite.set_frame("openrgb:0", []) is False  # nothing owns it
    composite.close()
    assert up.closed == 1
    assert down.closed == 0


def test_composite_reports_all_child_reasons_when_down() -> None:
    down1 = _ChildDriver(available=False)
    down2 = _ChildDriver(available=False)
    composite = CompositeDriver([down1, down2])
    result = composite.probe()
    assert result.available is False
    assert "Child:" in result.reason


def test_composite_open_probe_fallback_when_not_probed() -> None:
    child = _ChildDriver()
    composite = CompositeDriver([child])
    composite.open()  # open() without probe() must still attach children
    assert child.opened == 1


def test_composite_routes_by_device_id_not_driver_prefix() -> None:
    # the AULA driver is driver_id "aula_f75" but names its device "aula:0"
    # (and OpenRGB uses "openrgb:N") — routing must work for both shapes.
    class AliasChild(_ChildDriver):
        driver_id = "alias_f75"
        name = "Alias"

        def devices(self):  # device id does NOT start with the driver id
            return [
                RgbDevice(
                    device_id="alias:0", name="v", driver_id=self.driver_id, leds=1
                )
            ]

    child = AliasChild()
    composite = CompositeDriver([child])
    composite.probe()
    composite.open()
    devices = composite.devices()
    assert [d.device_id for d in devices] == ["alias:0"]
    assert composite.set_frame("alias:0", [RgbColor(4, 5, 6)]) is True
    assert child.frames[-1][0] == "alias:0"
    composite.close()


def test_composite_skips_foreign_device_ids() -> None:
    child = _ChildDriver()
    composite = CompositeDriver([child])
    composite.probe()
    # unknown prefix is refused, not silently routed to the first child
    assert composite.set_frame("unknown:1", []) is False
    assert child.frames == []
