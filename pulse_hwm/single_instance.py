from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# Single-instance hand-off. Windows re-launches the exe whenever a
# pulsehwm://auth-callback link is clicked — even while Pulse already
# runs in the tray. Without this guard, users get TWO copies and the
# callback dies in the fresh (token-less) process.
#
#   secondary instance → tries to connect to the named pipe
#     ├─ connected: passes its auth URL over the pipe and EXITS
#     └─ not connected: it IS the primary; starts the pipe server
#
# Elevation note: the primary often runs as admin while the browser
# launches the secondary non-elevated. The default pipe ACL blocks
# cross-integrity writes, so the server is created with WorldAccess —
# otherwise the handoff fails and a second window opens.

SERVER_NAME = "PulseHWM-single"
URL_PREFIX = "pulsehwm://"


def auth_urls_from_args(args: list[str] | tuple) -> list[str]:
    """Pull pulsehwm:// URLs out of a raw argv (pure; unit-tested)."""
    return [
        arg.strip()
        for arg in args
        if isinstance(arg, str) and arg.strip().lower().startswith(URL_PREFIX)
    ]


def try_handoff(args) -> bool:
    """True → continue as the primary instance. False → exit immediately
    (the running instance is about to process the URL)."""
    urls = auth_urls_from_args(args)
    # two attempts: a busy/elevated primary can miss the first connect
    for attempt in range(2):
        sock = QLocalSocket()
        sock.connectToServer(SERVER_NAME)
        if sock.waitForConnected(500):
            payload = "".join(u + "\n" for u in urls).encode()
            if payload:
                sock.write(payload)
                sock.flush()
                sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            return False
        sock.abort()
        if attempt == 0:
            time.sleep(0.15)
    return True


class SingleInstance(QObject):
    """Primary-instance pipe server: forwards auth URLs to the app."""

    url_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffers: dict[QLocalSocket, bytes] = {}
        # a crash can leave the pipe name stale — clear it first or
        # listen() would fail forever
        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer(self)
        # let a non-elevated secondary (browser-launched) hand off to an
        # elevated primary — default ACL would deny the write
        self._server.setSocketOptions(QLocalServer.SocketOption.WorldAccessOption)
        self._server.listen(SERVER_NAME)
        self._server.newConnection.connect(self._on_connection)

    def is_listening(self) -> bool:
        return self._server.isListening()

    def close(self) -> None:
        self._server.close()

    # ── internals ─────────────────────────────────────────────────────
    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                continue
            self._buffers[sock] = b""
            sock.readyRead.connect(self._make_reader(sock))
            sock.disconnected.connect(self._make_closer(sock))

    def _make_reader(self, sock: QLocalSocket):
        def read() -> None:
            self._buffers[sock] = self._buffers.get(sock, b"") + bytes(sock.readAll())
            text = self._buffers[sock].decode("utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line.lower().startswith(URL_PREFIX):
                    self.url_received.emit(line)

        return read

    def _make_closer(self, sock: QLocalSocket):
        def close() -> None:
            self._buffers.pop(sock, None)
            sock.deleteLater()

        return close
