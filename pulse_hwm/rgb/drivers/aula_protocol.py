"""AULA F75 / SinoWealth HID protocol codec — PURE bytearray math, no I/O.

Grounded in the community-reverse-engineered spec (marcoslor/Aula-F87-Controller
docs/PROTOCOL.md, confirmed on the F75 — same SinoWealth firmware family,
VID 0x258A). Attribution lives in docs/RGB.md + THIRD_PARTY.md (phase 25).

The wire format: 20-byte output reports —
    [0] report id 0x13
    [1] command (0x44 read, 0x04 write, 0x09 palette, 0x02 per-key, 0x0A save)
    [2] sub-command (0x0A config, 0x25 palette, 0x1C per-key, 0x01 confirm)
    [3] fragment sequence
    [4..18] 15-byte payload
    [19] checksum = sum(bytes 0..18) & 0xFF

The keyboard echoes every fragment; message sequences are:
  effect change = read(1) → write(10) → palette(37) → save(1)
  per-key       = read(1) → write config→eff21(10) → per-key map(28) → save(1)

Critical firmware quirk (the one that cost the reverse engineer days):
config fragment 0 byte 14 (apply flag) MUST be 0x00 on write — the keyboard
sets it to 0x01 after applying, and a read-modify-write that copies it back
is silently ignored.

This file never touches HID: phase 7's driver feeds report buffers to
hidapi; everything here is testable against synthetic vectors.
"""

from __future__ import annotations

from pulse_hwm.rgb.model import RgbColor

REPORT_ID = 0x13
FRAGMENT_SIZE = 20
PAYLOAD_START = 4
PAYLOAD_SIZE = 15
DATA_MARKER = 0x0E  # per-key data fragments start each payload with 0x0E

CMD_READ = 0x44
CMD_WRITE = 0x04
CMD_COLOR = 0x09
CMD_PERKEY = 0x02
CMD_SAVE = 0x0A

SUBCMD_CONFIG = 0x0A
SUBCMD_CONFIRM = 0x01
SUBCMD_PALETTE = 0x25
SUBCMD_PERKEY = 0x1C

# config fragment 0 offsets (full-fragment indexes)
CFG_CONFIRM_FLAG = 8  # must be 0x01 on write
CFG_APPLY_FLAG = 14  # must be 0x00 on write (see module docstring)
CFG_EFFECT = 15  # 1-18 built-ins, 21 = per-key "self-define"
CFG_COLOR_MODE = 17  # 0x01 custom/single-color, 0x03 default

PERKEY_EFFECT = 21  # "Self_define" — the per-key map mode
CONFIG_FRAGMENTS = 10
PALETTE_FRAGMENTS = 37
PERKEY_FRAGMENTS = 28
PERKEY_VALUES_PER_FRAG = 14
PERKEY_PLANE_FRAGMENTS = 9
PERKEY_LED_COUNT = PERKEY_PLANE_FRAGMENTS * PERKEY_VALUES_PER_FRAG  # 126

COLOR_MODE_CUSTOM = 0x01
COLOR_MODE_DEFAULT = 0x03

PALETTE_SEQ_CUSTOM_SLOT = 0x01  # custom color lives in palette frag 1
PALETTE_SEQ_CUSTOM = 0x01  # custom color lives in palette fragment 1
PALETTE_LAST_SEQ = 0x24  # 0x5AA5 end marker
PERKEY_TRAILER_SEQ = 27


def checksum(fragment: bytes | bytearray | list[int]) -> int:
    """sum(bytes 0..18) & 0xFF — mirrors the keyboard's own validation."""
    return sum(fragment[:19]) & 0xFF


def build_fragment(
    command: int, subcmd: int, seq: int, payload: list[int] | bytes | bytearray
) -> list[int]:
    """One 20-byte fragment. Payloads longer than 15 bytes raise: a
    silently truncated frame would light the wrong keys instead of failing
    loudly, so strictness beats tolerance here."""
    if len(payload) > PAYLOAD_SIZE:
        raise ValueError(f"payload {len(payload)} > {PAYLOAD_SIZE} bytes")
    body = [REPORT_ID, command & 0xFF, subcmd & 0xFF, seq & 0xFF]
    body.extend(int(b) & 0xFF for b in payload)
    body += [0] * (PAYLOAD_START + PAYLOAD_SIZE - len(body))
    body.append(checksum(body))
    return body[:FRAGMENT_SIZE]


def is_valid_fragment(fragment: list[int] | bytes) -> bool:
    if len(fragment) != FRAGMENT_SIZE or fragment[0] != REPORT_ID:
        return False
    return checksum(fragment) == fragment[19]


def save_request() -> list[int]:
    """SAVE/CONFIRM fragment: commits the pending config to flash.
    Payload prefix 04 07 comes from the OEM app's capture."""
    return build_fragment(CMD_SAVE, SUBCMD_CONFIRM, 0, [0x04, 0x07])


def read_request() -> list[int]:
    """READ/CONFIRM fragment: starts phase 1 (keyboard replies 10 fragments)."""
    return build_fragment(CMD_READ, SUBCMD_CONFIRM, 0, [])


def parse_config_response(fragments: list[list[int]]) -> dict | None:
    """Validate + summarize the 10-fragment READ reply. Returns None when
    the shape is wrong (wrong count / bad checksum / wrong command)."""
    if len(fragments) != CONFIG_FRAGMENTS:
        return None
    for frag in fragments:
        if not is_valid_fragment(frag):
            return None
        if frag[1] != CMD_READ or frag[2] != SUBCMD_CONFIG:
            return None
    zero = fragments[0]
    return {
        "effect": zero[CFG_EFFECT],
        "color_mode": zero[CFG_COLOR_MODE],
        "fragments": [list(f) for f in fragments],
    }


def set_effect_on_config(
    fragments: list[list[int]],
    effect: int,
    color_mode: int = COLOR_MODE_CUSTOM,
    effect_brightness: int | None = None,
    effect_speed: int | None = None,
) -> list[list[int]]:
    """Read-modify-write: return WRITE-command fragments with the requested
    effect/color mode, keeping every byte the app doesn't own unchanged
    (per-effect speed/brightness tables, key mappings, …).

    Per the Per-Effect Table: brightness encodes 0-4 directly and the speed
    byte's high nibble is speed 0-4 with low nibble 0x7 (colorful) / 0x0
    (single color). Brightness/speed land in the shared per-effect slots.
    """
    if len(fragments) != CONFIG_FRAGMENTS:
        raise ValueError(f"expected {CONFIG_FRAGMENTS} config fragments")
    out = [list(f) for f in fragments]
    zero = out[0]
    zero[1] = CMD_WRITE
    zero[CFG_CONFIRM_FLAG] = 0x01  # mandatory on write
    zero[CFG_APPLY_FLAG] = 0x00  # the critical byte — see module docstring
    zero[CFG_EFFECT] = effect & 0xFF
    zero[CFG_COLOR_MODE] = color_mode & 0xFF
    _apply_effect_settings(out, effect, effect_brightness, effect_speed)
    for frag in out:
        frag[19] = checksum(frag)
    return out


_EFFECT_SLOT: dict[int, tuple[int, int]] = (
    {n: (4, 7 + (n - 1) * 2) for n in range(1, 7)}
    | {n: (5, 5 + (n - 7) * 2) for n in range(7, 14)}
    | {n: (6, 5 + (n - 14) * 2) for n in range(14, 19)}
)


def _apply_effect_settings(
    fragments: list[list[int]], effect: int, brightness: int | None, speed: int | None
) -> None:
    """Write the per-effect [brightness][speed_byte] pair for `effect`."""
    slot = _EFFECT_SLOT.get(effect)
    if (brightness is None and speed is None) or slot is None:
        return
    frag_index, offset = slot
    if brightness is not None:
        fragments[frag_index][offset] = max(0, min(4, int(brightness)))
    if speed is not None:
        current = fragments[frag_index][offset + 1]
        # high nibble = speed 0-4; low nibble = color mode (0x7 colorful /
        # 0x0 single) — preserve the mode the app already chose
        speed_nibble = max(0, min(4, int(speed))) << 4
        mode_nibble = current & 0x0F
        fragments[frag_index][offset + 1] = speed_nibble | mode_nibble


def brightness_level(percent: int) -> int:
    """0-100% → firmware 0-4 (five discrete levels)."""
    return max(0, min(4, round(max(0, min(100, percent)) / 25)))


def to_speed_nibble(speed01to4: int, colorful: bool) -> int:
    """Speed 0-4 + color mode → the packed speed byte."""
    speed = max(0, min(4, int(speed01to4))) << 4
    return speed | (0x07 if colorful else 0x00)


def palette_fragments(
    color: RgbColor, palette_config: list[list[int]] | None = None
) -> list[list[int]]:
    """Phase 3: 37 CMD_COLOR/SUBCMD_PALETTE fragments carrying the custom
    color slot. The OEM app sends the whole palette on EVERY effect change,
    even for colorful-only effects — do the same for state consistency.
    `palette_config` (when provided, from a previous read) is respected
    byte-for-byte except the slots this build owns."""
    fragments = []
    for seq in range(PALETTE_FRAGMENTS):
        payload = [0] * PAYLOAD_SIZE
        if palette_config is not None and seq < len(palette_config):
            source = palette_config[seq]
            payload = list(source[PAYLOAD_START : PAYLOAD_START + PAYLOAD_SIZE])
        if seq == PALETTE_SEQ_CUSTOM:
            payload[4] = color.r  # offsets 8/9/10 of the full fragment
            payload[5] = color.g
            payload[6] = color.b
            payload[8] = 0xFF  # custom-color-active flag
        if seq == PALETTE_LAST_SEQ:
            payload[0] = 0x5A
            payload[1] = 0xA5
        fragments.append(build_fragment(CMD_COLOR, SUBCMD_PALETTE, seq, payload))
    return fragments


def perkey_fragments(colors: list[RgbColor]) -> list[list[int]]:
    """28 CMD_PERKEY/SUBCMD_PERKEY fragments: planar R/G/B maps (9 fragments
    × 14 values per plane covering 126 LED indices) + the 0x5A 0xA5 trailer.
    LED LED index = fragment_within_plane × 14 + byte_position."""

    def plane(channel: int) -> list[int]:
        values: list[int] = []
        for index in range(PERKEY_LED_COUNT):
            values.append(
                colors[index].r
                if channel == 0
                else colors[index].g if channel == 1 else colors[index].b
            )
        return values

    red, green, blue = plane(0), plane(1), plane(2)
    fragments: list[list[int]] = []
    for seq in range(PERKEY_FRAGMENTS):
        if seq == PERKEY_TRAILER_SEQ:
            payload = [0] * PAYLOAD_SIZE
            payload[0] = 0x06
            payload[3] = 0x5A
            payload[4] = 0xA5
            fragments.append(build_fragment(CMD_PERKEY, SUBCMD_PERKEY, seq, payload))
            continue
        plane_index, frag_in_plane = divmod(seq, PERKEY_PLANE_FRAGMENTS)
        start = frag_in_plane * PERKEY_VALUES_PER_FRAG
        channel = (red, green, blue)[plane_index]
        payload = [DATA_MARKER] + list(channel[start : start + PERKEY_VALUES_PER_FRAG])
        fragments.append(build_fragment(CMD_PERKEY, SUBCMD_PERKEY, seq, payload))
    return fragments
