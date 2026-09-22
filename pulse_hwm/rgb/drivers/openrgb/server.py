"""OpenRGB headless-server lifecycle — Qt-free subprocess management.

The backend architecture: Pulse spawns a HEADLESS OpenRGB (the bundled
`OpenRGB.exe --server`; run with --server and without --gui the process is
a pure daemon on a TCP port) OR connects to one the owner already runs.
We only ever terminate a process we spawned ourselves.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

from pulse_hwm.rgb.drivers.openrgb import protocol as P


class OrgbServerNotFound(FileNotFoundError):
    """No bundled/configured server binary exists on this machine."""


class OrgbServerNotStarted(RuntimeError):
    """The spawned server never came up (early exit / start timeout)."""


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_bundled() -> str:
    """Locate the bundled OpenRGB.exe. Cores: a configured override, the
    PyInstaller onedir app dir, or the dev repo's pulse_hwm/assets dir."""
    if hasattr(sys, "frozen"):
        roots = [os.path.dirname(sys.executable)]
        if hasattr(sys, "_MEIPASS"):
            roots.append(sys._MEIPASS)  # noqa: SLF001 — PyInstaller data root
    base = os.path.dirname(os.path.abspath(__file__))
    repo = base
    for _ in range(4):  # rgb/drivers/openrgb → rgb → drivers?? walk to repo
        repo = os.path.dirname(repo)
    roots = (list(roots) if "roots" in dir() else []) + [repo]

    for root in roots:
        for probe in (
            os.path.join(root, "openrgb", "OpenRGB.exe"),
            os.path.join(root, "pulse_hwm", "assets", "openrgb", "OpenRGB.exe"),
        ):
            if os.path.isfile(probe):
                return probe
    raise OrgbServerNotFound("OpenRGB.exe not found under app assets")


class OrgbServer:
    """Spawn/attach policy for one SDK server endpoint."""

    def __init__(self, port: int = P.DEFAULT_PORT) -> None:
        self._port = port
        self._proc: subprocess.Popen[bytes] | None = None  # OUR child only

    @property
    def port(self) -> int:
        return self._port

    def is_port_open(self) -> bool:
        """True when SOMETHING serves the SDK on our port right now (either
        a leftover of ours or a server the owner started)."""
        return _port_open(self._port)

    def ensure_running(self) -> str:
        """Make sure a server answers on the port; return the binary path
        used ("" when an already-running server was found)."""
        if self.is_port_open():
            return ""
        exe = _find_bundled()
        kwargs: dict = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if sys.platform == "win32"
            else {}
        )
        proc = subprocess.Popen(
            [exe, "--server", "--server-port", str(self._port)],
            **kwargs,
        )
        deadline = time.monotonic() + 60.0  # first run does full HW detection
        while time.monotonic() < deadline:
            if self.is_port_open():
                self._proc = proc
                return exe
            if proc.poll() is not None:
                raise OrgbServerNotStarted(
                    f"OpenRGB exited early with rc={proc.returncode}"
                )
            time.sleep(0.25)
        proc.terminate()
        raise OrgbServerNotStarted("OpenRGB --server never took the port (60s)")

    def stop(self) -> None:
        """Best-effort terminate of OUR child; foreign servers are left."""
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except OSError:
            pass
