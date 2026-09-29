"""OpenRGB protocol codec tests — pure parsing/serialization, no socket."""

from __future__ import annotations

import struct

import pytest

from pulse_hwm.rgb.drivers.openrgb import protocol as P


def _str(text: str) -> bytes:
    encoded = text.encode() + b"\x00"
    return struct.pack("<H", len(encoded)) + encoded


def _build_device_block() -> bytes:
    """A hand-built protocol-5 controller data block for one fake device:
    1 mode, 2 zones (single + linear), 3 LEDs, 0 colors."""
    body = struct.pack("<i", P.DEVICE_TYPE_MOTHERBOARD)  # type
    body += _str("MSI MAG B660 TEST")
    body += _str("MSI")  # vendor (v1)
    body += _str("Description of the fake board")
    body += _str("Controller version")
    body += _str("SN-000")
    body += _str(r"HID: \\?\HID#VID_1462&PID_7D41#6&0000#{uid}")

    # modes
    body += struct.pack("<H", 1)  # num_modes
    body += struct.pack("<i", 0)  # active_mode (BEFORE the mode blocks)
    mode = _str("Direct mode")
    mode += struct.pack("<i", 0)  # value (<6)
    mode += struct.pack("<I", 0) * 9  # flags/speeds/brightness/color bounds
    mode += struct.pack("<I", 0)  # direction
    mode += struct.pack("<I", 0)  # color_mode
    mode += struct.pack("<H", 0)  # mode colors count
    body += mode

    # zones: one single zone + one linear zone with one segment each
    body += struct.pack("<H", 2)  # num_zones
    z1 = _str("JRGB1") + struct.pack("<i", 0) + struct.pack("<III", 1, 1, 1)
    z1 += struct.pack("<H", 0)  # no matrix
    z1 += struct.pack("<H", 1)  # one segment (v4 always present)
    z1 += _str("seg") + struct.pack("<iII", 0, 0, 1) + struct.pack("<H", 0)
    z1 += struct.pack("<I", 0)  # zone flags (v5)
    body += z1
    z2 = _str("JRAINBOW1") + struct.pack("<i", 1) + struct.pack("<III", 1, 60, 60)
    z2 += struct.pack("<H", 0) + struct.pack("<H", 1)
    z2 += _str("seg") + struct.pack("<iII", 0, 0, 60) + struct.pack("<H", 0)
    z2 += struct.pack("<I", 0)
    body += z2

    # LEDs
    body += struct.pack("<H", 1 + 1 + 60 + 60)  # num_leds
    for i in range(122):
        body += _str(f"LED {i}") + struct.pack("<I", i)

    # device colors + LED display names + controller flags (v5)
    body += struct.pack("<H", 1) + struct.pack("<I", 0x0000FF)  # one red color
    body += struct.pack("<H", 2)  # num display names
    body += _str("Zone title")
    body += _str("Per-LED title")
    body += struct.pack("<I", 0)  # controller flags

    return struct.pack("<I", len(body)) + body


def test_header_roundtrip() -> None:
    frame = P.encode_header(2, P.PKT_REQUEST_CONTROLLER_DATA, b"abc")
    assert frame[:4] == b"ORGB"
    dev, pkt, size = P.decode_header(frame[:16])
    assert (dev, pkt, size) == (2, 1, 3)
    assert frame[16:] == b"abc"


def test_decode_header_rejects_bad_magic() -> None:
    bad = b"XXXX" + struct.pack("<III", 0, 0, 0)
    with pytest.raises(P.ProtocolError):
        P.decode_header(bad)


def test_update_leds_encoding() -> None:
    payload = P.encode_update_leds([(255, 0, 0), (0, 255, 0)])
    data_size = struct.unpack_from("<I", payload, 0)[0]
    # OpenRGB validates this against the SDK packet payload size, so it
    # includes the four-byte size field itself.
    assert data_size == len(payload)
    count = struct.unpack_from("<H", payload, 4)[0]
    assert count == 2
    assert struct.unpack_from("<I", payload, 6)[0] == 0xFF  # red
    assert struct.unpack_from("<I", payload, 10)[0] == 0xFF00  # green
    assert len(payload) == 4 + 2 + 8  # data_size + count + 2 words


def test_parse_controller_data_block_parses_zones_and_leds() -> None:
    payload = _build_device_block()
    controller = P.parse_controller_data(payload)
    assert controller.name == "MSI MAG B660 TEST"
    assert controller.vendor == "MSI"
    assert controller.device_type == P.DEVICE_TYPE_MOTHERBOARD
    assert controller.location.startswith("HID:")
    assert controller.led_count == 122
    zone_names = [zone.name for zone in controller.zones]
    assert zone_names == ["JRGB1", "JRAINBOW1"]
    assert controller.zones[0].zone_type == P.ZONE_TYPE_SINGLE
    assert controller.zones[0].leds_count == 1
    assert controller.zones[1].zone_type == P.ZONE_TYPE_LINEAR
    assert controller.zones[1].leds_count == 60


def test_parse_controller_data_raises_on_truncation() -> None:
    with pytest.raises(P.ProtocolError):
        payload = _build_device_block()
        P.parse_controller_data(payload[: len(payload) // 2])


def test_parse_controller_data_reads_active_mode_and_stored_colors() -> None:
    """The driver's read-back check depends on these two fields."""
    controller = P.parse_controller_data(_build_device_block())
    assert controller.mode_names == ("Direct mode",)
    assert controller.active_mode == 0
    assert controller.active_mode_name == "Direct mode"
    assert controller.colors == (P.pack_color(255, 0, 0),)


def test_pack_color_matches_update_leds_words() -> None:
    payload = P.encode_update_leds([(1, 2, 3)])
    assert struct.unpack_from("<I", payload, 6)[0] == P.pack_color(1, 2, 3)
