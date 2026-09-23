"""OpenRGB SDK client — blocking loopback socket, Qt-free.

Used ONLY from the rgb worker thread (attach + set_frame happen there), so
a plain blocking socket is correct and simple. For tests the socket module
is injected: tests spin up an in-process fake SDK server and hand us a
socket factory that points at it — no real network involved.

Protocol notes (see protocol.py for packet formats):
  * The server may interleave notifications (ACK / DEVICE_LIST_UPDATED /
    DETECTION_* / SIGNALUPDATE) on the socket at any time. Every wait loop
    therefore skips foreign packet IDs instead of trusting ordering.
  * RGB packets (UPDATELEDS / SETCUSTOMMODE) get NO reply in protocol 5 —
    they are fire-and-forget at render rate.
"""

from __future__ import annotations

import socket
import struct
import time

from pulse_hwm.rgb.drivers.openrgb import protocol as P


class OrgbClient:
    """One connection to one OpenRGB SDK server (protocol 5 semantics)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = P.DEFAULT_PORT,
        timeout: float = P.DEFAULT_TIMEOUT_S,
        client_name: str = "Pulse-HWM",
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._client_name = client_name
        self._sock: socket.socket | None = None
        self._proto = 0
        self._controllers: list[P.OrgbController] = []

    # ── lifecycle ─────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Negotiate protocol 5, read the controller list. Raises
        OSError/ProtocolError; callers catch and degrade."""
        sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        sock.settimeout(self._timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._send(
            P.PKT_SET_CLIENT_NAME,
            (self._client_name + "\x00").encode("ascii", "replace"),
        )

        # Version negotiation: send our max (5), server replies its max; the
        # connection protocol = min(ours, server). Cap at 5 for one parser.
        proto = self._request_version()
        if proto > P.PROTOCOL_MAX:
            proto = P.PROTOCOL_MAX
        self._proto = proto
        self._refresh_controllers()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def controllers(self) -> list[P.OrgbController]:
        return list(self._controllers)

    def refresh(self) -> None:
        """Re-read the device list (after DEVICE_LIST_UPDATED / rescan)."""
        self._ensure_open()
        self._refresh_controllers()

    def rescan(self) -> None:
        """Ask the server to rescan hardware (id 140 → controller ids 0..n-1
        may become invalid; callers must call refresh() next)."""
        self._ensure_open()
        self._send(P.PKT_REQUEST_RESCAN_DEVICES, b"")

    # ── device data readout ───────────────────────────────────────────────

    def _request_count(self) -> list[int]:
        self._send(P.PKT_REQUEST_CONTROLLER_COUNT, b"")
        _, _, payload = self._await_packet(P.PKT_REQUEST_CONTROLLER_COUNT)
        if len(payload) < 4:
            raise P.ProtocolError("count response too short")
        count = struct.unpack_from("<I", payload, 0)[0]
        # v5 = indexed scheme: the ids ARE the indices. (v6 would append a
        # unique-id list; our negotiated cap of 5 means we never see it.)
        return list(range(count))

    def _refresh_controllers(self) -> None:
        # The server answers immediately after its port opens, but HARDWARE
        # DETECTION runs after that (many seconds on a first run). A count
        # request fired too early legitimately returns 0 — poll until the
        # server reports at least one controller or the deadline expires
        # (a machine with genuinely no RGB hardware exits the loop into a
        # clean empty list rather than hanging the rgb thread forever).
        deadline = time.monotonic() + 90.0
        ids: list[int] = []
        while True:
            ids = self._request_count()
            if ids or time.monotonic() > deadline:
                break
            time.sleep(1.0)
        found: list[P.OrgbController] = []
        for idx in ids:
            payload = struct.pack("<I", self._proto)
            self._send_dev(idx, P.PKT_REQUEST_CONTROLLER_DATA, payload)
            _, _, data = self._await_packet(P.PKT_REQUEST_CONTROLLER_DATA, dev=idx)
            controller = P.parse_controller_data(data)
            replaced = P.OrgbController(
                index=idx,
                device_type=controller.device_type,
                name=controller.name,
                vendor=controller.vendor,
                description=controller.description,
                version=controller.version,
                serial=controller.serial,
                location=controller.location,
                led_count=controller.led_count,
                zones=controller.zones,
            )
            found.append(replaced)
        self._controllers = found

    # ── rendering ────────────────────────────────────────────────────────

    def set_custom_mode(self, index: int) -> None:
        """Switch a device to DIRECT/custom mode so UPDATELEDS is honored.
        Fire-and-forget (no reply in this protocol version)."""
        self._ensure_open()
        self._send_dev(index, P.PKT_RGB_SETCUSTOMMODE, b"")

    def update_leds(self, index: int, colors: list[tuple[int, int, int]]) -> None:
        """Push one full frame. colors length MUST equal device led_count.
        Fire-and-forget; the server applies positionally from LED 0."""
        self._ensure_open()
        if len(colors) == 0:
            return  # nothing to push; avoids a malformed empty packet
        self._send_dev(index, P.PKT_RGB_UPDATELEDS, P.encode_update_leds(colors))

    # ── packet helpers ────────────────────────────────────────────────────

    def _request_version(self) -> int:
        """VERSIONREQUEST (id 40): send our max, return the server's max.
        Receiving nothing = a v0 server (documented behavior)."""
        self._send(P.PKT_REQUEST_PROTOCOL_VERSION, struct.pack("<I", P.PROTOCOL_MAX))
        _, _, payload = self._await_packet(P.PKT_REQUEST_PROTOCOL_VERSION)
        if len(payload) < 4:
            raise P.ProtocolError("version response too short")
        return struct.unpack_from("<I", payload, 0)[0]

    def _ensure_open(self) -> None:
        if self._sock is None:
            raise P.ProtocolError("client not connected")

    def _send(self, pkt_id: int, payload: bytes, dev: int = 0) -> None:
        assert self._sock is not None
        self._sock.sendall(P.encode_header(dev, pkt_id, payload))

    def _send_dev(self, dev: int, pkt_id: int, payload: bytes) -> None:
        self._send(pkt_id, payload, dev=dev)

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        chunks = bytearray()
        while len(chunks) < n:
            chunk = self._sock.recv(n - len(chunks))
            if not chunk:
                raise P.ProtocolError("connection closed mid-packet")
            chunks.extend(chunk)
        return bytes(chunks)

    def _await_packet(
        self, want_id: int, dev: int | None = None
    ) -> tuple[int, int, bytes]:
        """Read frames until the requested response arrives, skipping every
        server-initiated notification on the way. Detection may also RACE
        our request, so a bounded number of skips keeps us honest."""
        assert self._sock is not None
        for _ in range(256):
            head = self._recv_exact(16)
            got_dev, got_id, size = P.decode_header(head)
            payload = self._recv_exact(size) if size else b""
            want_dev = True if dev is None else got_dev == dev
            if got_id == want_id and want_dev:
                return got_dev, got_id, payload
            if got_id in P.NOTIFICATION_IDS:
                continue
        raise P.ProtocolError(f"no reply for packet {want_id} (dev={dev})")
