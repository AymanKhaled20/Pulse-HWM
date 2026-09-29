"""OpenRGB SDK protocol codec — Qt-free, pure, fully unit-testable.

Reference: Documentation/OpenRGBSDK.md in the OpenRGB repo (GPL-2.0).
We implement protocol 5 only:

  * v4+ segment info is ALWAYS present inside zone blocks (needed to step
    through them), so the parser is protocol-4 shaped.
  * v5 adds zone flags + controller flags + LED display names, which we
    parse and drop so offsets stay correct.
  * v6 changes mode/led framing (value fields removed) and adds per-zone
    mode lists we do not use — clamping the negotiated version to 5 keeps
    ONE parser and keeps the client on the simple indexed device-ID scheme.

Everything here is pure data-in/data-out; no sockets, no Qt. The socket
side lives in client.py.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"ORGB"
PROTOCOL_MAX = 5  # we never speak above this

HEAD_FMT = "<4sIII"  # magic, dev_id, pkt_id, pkt_size
HEAD_LEN = struct.calcsize(HEAD_FMT)

DEFAULT_PORT = 6742
DEFAULT_TIMEOUT_S = 15.0

# Packet IDs (the ones we use)
PKT_REQUEST_CONTROLLER_COUNT = 0
PKT_REQUEST_CONTROLLER_DATA = 1
PKT_REQUEST_PROTOCOL_VERSION = 40
PKT_SET_CLIENT_NAME = 50
PKT_DEVICE_LIST_UPDATED = 100
PKT_REQUEST_RESCAN_DEVICES = 140
PKT_RGB_RESIZEZONE = 1000
PKT_RGB_UPDATELEDS = 1050
PKT_RGB_SETCUSTOMMODE = 1100

# Server-initiated notification IDs the client must skip over
NOTIFICATION_IDS = frozenset(
    {
        10,  # ACK
        51,
        53,  # server name/flags
        100,  # DEVICE_LIST_UPDATED
        101,
        102,
        103,  # detection started/progress/complete
        300,
        301,
        304,  # log-manager pushes
        1150,  # SIGNALUPDATE callbacks
    }
)

# Zone/Device types worth naming for layout mapping
ZONE_TYPE_SINGLE = 0
ZONE_TYPE_LINEAR = 1
ZONE_TYPE_MATRIX = 2

DEVICE_TYPE_MOUSE = 6
DEVICE_TYPE_KEYBOARD = 5
DEVICE_TYPE_HEADSET = 8
DEVICE_TYPE_MOTHERBOARD = 0


class ProtocolError(Exception):
    """Malformed/out-of-order SDK exchange. Callers degrade, never crash."""


def encode_header(dev_id: int, pkt_id: int, payload: bytes) -> bytes:
    """One SDK frame: header + payload. dev_id = controller index (v5)."""
    return MAGIC + struct.pack("<III", dev_id, pkt_id, len(payload)) + payload


def decode_header(frame_head: bytes) -> tuple[int, int, int]:
    """Interpret the 16-byte frame head: (dev_id, pkt_id, payload_size)."""
    if len(frame_head) < HEAD_LEN:
        raise ProtocolError(f"short head: {len(frame_head)} < {HEAD_LEN}")
    magic, dev, pkt, size = struct.unpack(HEAD_FMT, frame_head)
    if magic != MAGIC:
        raise ProtocolError(f"bad magic: {magic!r}")
    return dev, pkt, size


@dataclass(frozen=True)
class OrgbZone:
    """One zone (named LED group) from a controller data block."""

    name: str
    zone_type: int  # 0=single, 1=linear, 2=matrix
    leds_count: int


@dataclass(frozen=True)
class OrgbController:
    """Parsed RGBController data block (protocol 5)."""

    index: int  # server-side device index used for all subsequent packets
    device_type: int  # RGBController DEVICE_TYPE enum
    name: str
    vendor: str
    description: str
    version: str
    serial: str
    location: str
    led_count: int
    zones: tuple[OrgbZone, ...] = field(default_factory=tuple)
    # read back so the driver can CHECK what the server really holds:
    # which mode is active, and the colors it last accepted (packed words,
    # see pack_color). Defaults keep hand-built test controllers short.
    active_mode: int = -1
    mode_names: tuple[str, ...] = field(default_factory=tuple)
    colors: tuple[int, ...] = field(default_factory=tuple)

    @property
    def active_mode_name(self) -> str:
        if 0 <= self.active_mode < len(self.mode_names):
            return self.mode_names[self.active_mode]
        return "?"


class _Reader:
    """Cursor over one controller-data payload with bounds checking — a
    truncated/malformed block raises ProtocolError, never IndexError."""

    def __init__(self, payload: bytes) -> None:
        self._buf = payload
        self._pos = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self._pos + n > len(self._buf):
            raise ProtocolError(
                f"short read: want {n} at {self._pos} of {len(self._buf)}"
            )
        out = self._buf[self._pos : self._pos + n]
        self._pos += n
        return out

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def strz(self) -> str:
        """u16 length (INCLUDING the NUL) + bytes; NUL stripped."""
        return self.take(self.u16())[:-1].decode("utf-8", "replace")


def _consume_mode(d: _Reader) -> str:
    """Step over one Mode Data block (name + all fields incl. colors) and
    return the mode's name."""
    mode_name = d.strz()
    d.i32()  # mode_value (present at v5, dropped at v6)
    d.u32()  # flags
    d.u32()  # speed_min
    d.u32()  # speed_max
    d.u32()  # brightness_min (v3)
    d.u32()  # brightness_max (v3)
    d.u32()  # colors_min
    d.u32()  # colors_max
    d.u32()  # speed
    d.u32()  # brightness (v3)
    d.u32()  # direction
    d.u32()  # color_mode
    colors = d.u16()
    d.take(4 * colors)
    return mode_name


def parse_controller_data(payload: bytes) -> OrgbController:
    """Parse one REQUEST_CONTROLLER_DATA response (protocol 5 semantics).

    Returns a frozen dataclass — it crosses threads (rgb worker → UI), the
    same way every other object in the rgb package does.
    """
    outer = _Reader(payload)
    outer.u32()  # outer data_size (informational; we parse what follows)
    d = outer  # the device data block IS the rest of the payload

    device_type = d.i32()
    name = d.strz()
    vendor = d.strz()  # v1: vendor
    description = d.strz()
    version = d.strz()
    serial = d.strz()
    location = d.strz()

    num_modes = d.u16()
    active_mode = d.i32()  # comes BEFORE the mode blocks
    mode_names = tuple(_consume_mode(d) for _m in range(num_modes))

    zones: list[OrgbZone] = []
    num_zones = d.u16()
    for _z in range(num_zones):
        zname = d.strz()
        ztype = d.i32()
        _ = d.u32()  # leds_min
        _ = d.u32()  # leds_max
        zleds = d.u32()
        mlen = d.u16()
        if mlen:
            d.take(mlen)  # matrix map block
        segs = d.u16()
        for _s in range(segs):
            _ = d.strz()  # segment name
            d.i32()  # segment type
            d.u32()  # start_idx
            d.u32()  # segment leds
            seg_mlen = d.u16()
            if seg_mlen:
                d.take(seg_mlen)
        _ = d.u32()  # zone flags (v5)
        zones.append(OrgbZone(name=zname, zone_type=ztype, leds_count=zleds))

    num_leds = d.u16()
    for _l in range(num_leds):
        _ = d.strz()  # led name
        d.u32()  # led value (present at v5)

    num_colors = d.u16()
    color_bytes = d.take(4 * num_colors)  # device color array
    colors = struct.unpack(f"<{num_colors}I", color_bytes) if num_colors else ()
    display_names = d.u16()  # v5: LED display names
    for _n in range(display_names):
        _ = d.strz()
    _ = d.u32()  # controller flags (v5)

    return OrgbController(
        index=0,  # assigned by the client after parsing
        device_type=device_type,
        name=name,
        vendor=vendor,
        description=description,
        version=version,
        serial=serial,
        location=location,
        led_count=num_leds,
        zones=tuple(zones),
        active_mode=active_mode,
        mode_names=mode_names,
        colors=tuple(colors),
    )


def pack_color(r: int, g: int, b: int) -> int:
    """One LED color as OpenRGB stores it: R | G<<8 | B<<16 (no alpha)."""
    return (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)


def encode_update_leds(colors: list[tuple[int, int, int]]) -> bytes:
    """Encode an ``RGBCONTROLLER_UPDATELEDS`` payload.

    Each color is one 32-bit LE word: R | G<<8 | B<<16 (alpha is unused).
    The count must cover the WHOLE device — OpenRGB's custom mode applies
    the array positionally from LED 0.

    OpenRGB's first field is the size of the complete color-description
    payload, including that four-byte size field itself.  Omitting those four
    bytes makes the server reject every update as an invalid-size packet.
    """
    body = struct.pack("<H", len(colors))
    for r, g, b in colors:
        body += struct.pack("<I", pack_color(r, g, b))
    return struct.pack("<I", len(body) + 4) + body
