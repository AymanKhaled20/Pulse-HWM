"""Razer matrix frame builder — openrazer envelope semantics, FIRST PASS.

Envelope (90 bytes total, openrazer mouse/kb common layout, unverified on
Windows hidapi until a live capture confirms):
    [0] status_sent        = 0xFF
    [1] transaction_id     = 0x3F
    [2:4] remaining_packets = 0
    [4] protocol_type      = 0x03
    [5] data_size          = payload byte count (varies per command)
    [6] command_class      = 0x0F 'Backlight LED matrix'
    [7] command_id         = 0x0A 'Set custom frame row'
    [8] args: row_index, column_start, column_end, R,G,B ...
"""

from __future__ import annotations

from typing import Iterator

from pulse_hwm.rgb.model import RgbColor

ENVELOPE_LEN = 90
STATUS_SENT = 0xFF
TRANSACTION_ID = 0x3F
PROTOCOL_TYPE = 0x03


def matrix_envelope(
    command_class: int, command_id: int, args: bytes, data_size: int | None = None
) -> bytes:
    if len(args) > ENVELOPE_LEN - 8:
        raise ValueError("razer args overflow")
    if data_size is None:
        data_size = len(args)
    out = bytearray(ENVELOPE_LEN)
    out[0] = STATUS_SENT
    out[1] = TRANSACTION_ID
    out[2:4] = (0, 0)  # remaining packets (little-endian-ish as spec pads)
    out[4] = PROTOCOL_TYPE
    out[5] = data_size
    out[6] = command_class
    out[7] = command_id
    out[8 : 8 + len(args)] = args
    return bytes(out)


def matrix_row_payloads(color: RgbColor, rows: int, row_size: int) -> Iterator[bytes]:
    """One envelope per row: uniform color across the row (custom-frame
    first pass honors whole-device color from _uniform's pre-processing)."""
    for row in range(rows):
        args = bytes((row, 0, row_size - 1, color.r, color.g, color.b))
        yield matrix_envelope(0x0F, 0x0A, args)


def memset_dummy() -> bytes:
    """Zero row command — exported for tests to assert envelope shape."""
    return matrix_envelope(0x0F, 0x0A, bytes((0, 0, 0, 0, 0, 0)))
