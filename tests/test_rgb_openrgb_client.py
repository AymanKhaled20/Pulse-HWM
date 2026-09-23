"""OpenRgbClient tests — against an in-process fake SDK server.

The fake server speaks just enough of the documented protocol-5 framing to
exercise the client's handshake, device readout, and the fire-and-forget
frame path. It binds port 0 (ephemeral) inside the test process only.
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from pulse_hwm.rgb.drivers.openrgb import protocol as P
from pulse_hwm.rgb.drivers.openrgb.client import OrgbClient

MAG = b"ORGB"


def _str(text: str) -> bytes:
    encoded = text.encode() + b"\x00"
    return struct.pack("<H", len(encoded)) + encoded


def _frame(dev: int, pkt_id: int, payload: bytes) -> bytes:
    return P.encode_header(dev, pkt_id, payload)


def _fake_controller_payload() -> bytes:
    """Protocol-5 device block for one fake 3-LED mouse."""
    body = struct.pack("<i", P.DEVICE_TYPE_MOUSE)
    body += _str("Test Mouse")
    body += _str("ACME")
    body += _str("desc")
    body += _str("v1")
    body += _str("serial")
    body += _str(r"HID: \\?\HID#VID_1234&PID_5678")
    body += struct.pack("<H", 1)  # num_modes
    body += struct.pack("<i", 0)  # active_mode (BEFORE mode blocks)
    body += _str("Direct")
    body += struct.pack("<i", 0)  # value (<6)
    body += struct.pack("<I", 0) * 9  # flags/speeds/brightness bounds
    body += struct.pack("<I", 0)  # direction
    body += struct.pack("<I", 0)  # color_mode
    body += struct.pack("<H", 0)  # mode colors
    body += struct.pack("<H", 1)  # num_zones
    body += _str("Mouse Zone") + struct.pack("<i", 1)
    body += struct.pack("<III", 1, 3, 3)
    body += struct.pack("<H", 0)  # no matrix
    body += struct.pack("<H", 1)  # one segment
    body += _str("seg") + struct.pack("<iII", 0, 0, 3) + struct.pack("<H", 0)
    body += struct.pack("<I", 0)  # zone flags
    body += struct.pack("<H", 3)  # num_leds
    for i in range(3):
        body += _str(f"LED{i}") + struct.pack("<I", i)
    body += struct.pack("<H", 0)  # device colors count
    body += struct.pack("<H", 1) + _str("Zone Display")  # display names
    body += struct.pack("<I", 0)  # controller flags
    return struct.pack("<I", len(body)) + body


class FakeSdkServer(threading.Thread):
    """Handles ONE client connection; answers the standard handshake."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.updates: list[tuple[int, bytes]] = []  # (dev_id, UPDATELEDS payload)
        self.custom_mode_calls: list[int] = []
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._running = True

    def run(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        while self._running:
            head = self._recv_exact(conn, 16)
            if head is None:
                break
            magic, dev, pkt_id, size = struct.unpack("<4sIII", head)
            assert magic == MAG, f"bad magic {magic!r}"
            payload = self._recv_exact(conn, size) if size else b""
            if pkt_id == P.PKT_REQUEST_PROTOCOL_VERSION:
                # server advertises its max (6); the client must clamp to 5
                conn.sendall(_frame(dev, 40, struct.pack("<I", 6)))
            elif pkt_id == P.PKT_REQUEST_CONTROLLER_COUNT:
                conn.sendall(_frame(dev, 0, struct.pack("<I", 1)))
            elif pkt_id == P.PKT_REQUEST_CONTROLLER_DATA:
                conn.sendall(_frame(dev, 1, _fake_controller_payload()))
            elif pkt_id == P.PKT_RGB_UPDATELEDS:
                self.updates.append((dev, payload))
            elif pkt_id == P.PKT_RGB_SETCUSTOMMODE:
                self.custom_mode_calls.append(dev)
        conn.close()

    def stop(self) -> None:
        self._running = False
        try:
            self._srv.close()
        except OSError:
            pass

    def _recv_exact(self, conn: socket.socket, n: int) -> bytes | None:
        chunks = bytearray()
        try:
            while len(chunks) < n:
                chunk = conn.recv(n - len(chunks))
                if not chunk:
                    return None
                chunks.extend(chunk)
        except OSError:
            return None
        return bytes(chunks)


class DetectionRacingServer(FakeSdkServer):
    """First controller-count request returns 0 (detection running), the
    second returns 1 — the client must poll instead of giving up."""

    count_requests = 0

    def _serve_one(self, conn: socket.socket, head: bytes) -> None:
        _, dev, pkt_id, size = struct.unpack("<4sIII", head)
        self._recv_exact(conn, size) if size else None  # drain the payload
        if pkt_id == P.PKT_REQUEST_PROTOCOL_VERSION:
            conn.sendall(_frame(dev, 40, struct.pack("<I", 6)))
        elif pkt_id == P.PKT_REQUEST_CONTROLLER_COUNT:
            DetectionRacingServer.count_requests += 1
            count = 0 if DetectionRacingServer.count_requests == 1 else 1
            conn.sendall(_frame(dev, 0, struct.pack("<I", count)))
        elif pkt_id == P.PKT_REQUEST_CONTROLLER_DATA:
            conn.sendall(_frame(dev, 1, _fake_controller_payload()))

    def run(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        while self._running:
            head = self._recv_exact(conn, 16)
            if head is None:
                break
            self._serve_one(conn, head)
        conn.close()


def _make_client(port: int) -> OrgbClient:
    return OrgbClient(port=port, timeout=2.0)


def test_client_polls_until_devices_appear() -> None:
    server = DetectionRacingServer()
    server.start()
    try:
        client = _make_client(server.port)
        client.connect()
        assert len(client.controllers) == 1
        assert server.count_requests >= 2
    finally:
        client.close()
        server.stop()


def test_client_handshake_and_readout() -> None:
    server = FakeSdkServer()
    server.start()
    try:
        client = _make_client(server.port)
        client.connect()
        controllers = client.controllers
        assert len(controllers) == 1
        controller = controllers[0]
        assert controller.index == 0
        assert controller.name == "Test Mouse"
        assert controller.vendor == "ACME"
        assert controller.led_count == 3
        assert controller.zones[0].name == "Mouse Zone"
        assert controller.zones[0].leds_count == 3
    finally:
        if "client" in locals():
            client.close()
        server.stop()


def test_client_update_frame_round_trip() -> None:
    server = FakeSdkServer()
    server.start()
    client = _make_client(server.port)
    try:
        client.connect()
        client.update_leds(0, [(10, 20, 30), (10, 20, 30), (10, 20, 30)])
        # give the fake server's thread a moment to record the frame
        for _ in range(50):
            if server.updates:
                break
            threading.Event().wait(0.02)
        assert len(server.updates) == 1
        dev, payload = server.updates[0]
        assert dev == 0
        count = struct.unpack_from("<H", payload, 4)[0]
        assert count == 3
        assert struct.unpack_from("<I", payload, 6)[0] == (10 | 20 << 8 | 30 << 16)
    finally:
        client.close()
        server.stop()


def test_client_rejects_wrong_port() -> None:
    with pytest.raises(OSError):
        OrgbClient(port=1, timeout=0.5).connect()
