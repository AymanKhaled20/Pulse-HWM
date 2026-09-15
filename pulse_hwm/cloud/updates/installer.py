"""Installer bridge â€” download, verify, and run the update installer.

Deliberately staged so every dangerous step is testable without touching
the network or spawning anything:

  download_installer()  streams ONE allowlisted URL into a staging file
                        (.part â†’ atomic rename) whose sha256 must equal
                        the SIGNED manifest value
  verify_installer()    re-hashes the exact file and (on Windows) runs
                        WinVerifyTrust â€” both immediately before spawn
  install_command()     the exact silent command line (single source)
  spawn_installer()     subprocess.Popen with an injectable callable

Failures never crash the app: the UpdateInstaller bridge reports
progress()/finished() and the app stays alive (a botched update must
never take down a working install).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from pulse_hwm.cloud.updates import trust
from pulse_hwm.cloud.updates.policy import (
    chunk_budget_exceeded,
    host_allowed,
    host_of,
    parse_manifest,
    resolve_download_urls,
    size_within_budget,
)

_DOWNLOAD_TIMEOUT_S = 30.0  # per read op; long transfers stream via idle timeout
_IDLE_TIMEOUT_S = 20.0
_CHUNK = 256 * 1024


class UpdateError(Exception):
    """Human-readable install failure (safe to show in the UI)."""


def _worker_hosts(worker_base: str) -> frozenset[str]:
    return frozenset({host_of(worker_base.rstrip("/"))})


def pick_download_url(release: dict, worker_base: str) -> str:
    """First candidate URL that is https AND on the trusted host list.

    The /dl/ worker path (R2 front-desk) comes first; the GitHub release
    asset is the resilience fallback. Unsigned decoration can therefore
    at most point us inside already-trusted hosts.
    """
    for url in resolve_download_urls(release, worker_base):
        if host_allowed(url, _worker_hosts(worker_base)):
            return url
    raise UpdateError("no trusted download host in this release")


def expect_sha256(release: dict) -> str:
    """The sha256 that the SIGNED manifest binds us to (never the decorated
    top-level copy alone — an unverified release dict must not pick it)."""
    signed = parse_manifest(str(release.get("manifest", "") or "")) or {}
    sha = str(signed.get("sha256", "") or "").lower()
    if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
        raise UpdateError("release has no signed sha256")
    return sha


def asset_name_of(release: dict) -> str:
    signed = parse_manifest(str(release.get("manifest", "")) or "") or {}
    name = str(signed.get("asset_name", "") or "")
    if (
        not name.startswith("PulseHWM-Setup-")
        or not name.endswith(".exe")
        # reject anything that can traverse: path separators / dot-dot
        or name != Path(name).name
    ):
        raise UpdateError("unexpected installer filename")
    return name


def download_installer(
    release: dict,
    worker_base: str,
    access_token: str,
    dest_dir: Path,
    on_progress=None,  # callable(done_bytes, total_bytes_or_None)
    transport=None,  # injectable httpx transport (tests use MockTransport)
) -> Path:
    """Stream the installer; returns the FULLY-VERIFIED (hash) path.

    Staged write (.part â†’ atomic rename) means a killed download can never
    leave a half-written installer pretending to be final.
    """
    import httpx

    url = pick_download_url(release, worker_base)
    expect = expect_sha256(release)
    asset = asset_name_of(release)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / asset

    headers = {"Authorization": f"Bearer {access_token}"}
    hasher = hashlib.sha256()
    done = 0
    total: int | None = None
    try:
        client_kwargs = {
            "timeout": httpx.Timeout(_DOWNLOAD_TIMEOUT_S, read=_IDLE_TIMEOUT_S),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        with httpx.Client(follow_redirects=True, **client_kwargs) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    raise UpdateError(f"download failed (http {resp.status_code})")
                length = resp.headers.get("content-length")
                if length is not None:
                    total = int(length)
                    if not size_within_budget(total):
                        raise UpdateError("declared installer size is out of budget")
                with open(staging_path(final_path), "wb") as fh:
                    for chunk in resp.iter_bytes(_CHUNK):
                        done += len(chunk)
                        hasher.update(chunk)
                        if chunk_budget_exceeded(done):
                            raise UpdateError("installer download too large")
                        fh.write(chunk)
                        if on_progress is not None:
                            on_progress(done, total)
    except UpdateError:
        cleanup_staging(final_path)
        raise
    except Exception as exc:
        cleanup_staging(final_path)
        raise UpdateError(f"download failed: {exc}") from exc

    actual = hasher.hexdigest()
    if actual != expect:
        cleanup_staging(final_path)
        raise UpdateError("checksum mismatch â€” installer not tamper-safe, aborted")
    os.replace(staging_path(final_path), final_path)
    return final_path


def staging_path(final_path: Path) -> Path:
    return final_path.with_name(final_path.name + ".part")


def cleanup_staging(final_path: Path) -> None:
    try:
        Path(staging_path(final_path)).unlink(missing_ok=True)
    except OSError:
        pass


def verify_installer(path: Path, expect_sha: str) -> None:
    """Final verification, called AGAIN immediately before spawn (TOCTOU
    shrink window). Raises UpdateError on any mismatch."""
    path = Path(path)
    if not path.exists():
        raise UpdateError("installer vanished before install")
    if path.stat().st_size <= 0:
        raise UpdateError("installer file is empty")
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != expect_sha:
        try:
            Path(path).unlink(missing_ok=True)  # tampered on disk â€” destroy it
        except OSError:
            pass
        raise UpdateError("checksum no longer matches â€” refusing to install")
    if trust.authenticode_available() and not trust.authenticode_verified(str(path)):
        # defense-in-depth: enforced once SignPath signs production
        # releases (flip trust.AUTHENTICODE_REQUIRED)
        if trust.AUTHENTICODE_REQUIRED:
            raise UpdateError("installer is not Authenticode-signed")


def install_command(setup_path: Path | str) -> list[str]:
    """The silent, controlled install line.

    /SILENT            â†’ visible progress, no prompts
    /NORESTART         â†’ Windows decides nothing; we relaunch ourselves
    /LAUNCHAFTER=1     â†’ the .iss [Code] check relaunches Pulse afterwards
    """
    return [str(setup_path), "/SILENT", "/NORESTART", "/LAUNCHAFTER=1"]


def spawn_installer(cmd: list[str], spawn=None) -> None:  # spawn injectable for tests
    proc = (spawn or subprocess.Popen)(
        cmd,
        creationflags=(
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "DETACHED_PROCESS")
            else 0
        ),
        close_fds=True,
    )
    return proc


# â€”â€” bridge: QThreadPool stages + Signals â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”


@dataclass
class InstallOutcome:
    ok: bool = False
    error: str = ""
    version: str = ""
    installer_path: str = ""


class _InstallSignals(QObject):
    progress = Signal(int, int)  # done_bytes, total_bytes (-1 unknown)
    finished = Signal(object)  # InstallOutcome


class _InstallTask(QRunnable):
    def __init__(self, release, worker_base, session, dest_dir, signals):
        super().__init__()
        self._release = dict(release)
        self._worker_base = worker_base
        self._session = session
        self._dest_dir = Path(dest_dir)
        self._signals = signals

    def run(self) -> None:  # pool thread
        outcome = InstallOutcome(ok=False)
        try:
            outcome = self._cycle()
        except UpdateError as exc:
            outcome = InstallOutcome(ok=False, error=str(exc))
        except Exception as exc:  # defense: never dead-silence a pool crash
            outcome = InstallOutcome(ok=False, error=f"unexpected: {exc}")
        self._signals.finished.emit(outcome)

    def _cycle(self) -> InstallOutcome:
        # re-run the gate at THIS layer too (defense in depth)
        expect = expect_sha256(self._release)
        installer = download_installer(
            self._release,
            self._worker_base,
            self._session.bearer(),
            self._dest_dir,
            on_progress=self._progress,
        )
        verify_installer(installer, expect)
        cmd = install_command(installer)
        spawn_installer(cmd)
        return InstallOutcome(
            ok=True,
            error="",
            version=str(self._release.get("version", "")),
            installer_path=str(installer),
        )

    def _progress(self, done: int, total) -> None:
        self._signals.progress.emit(done, -1 if total is None else int(total))


class UpdateInstaller(QObject):
    """Client-side face: install(release) â†’ progress/finished Signals."""

    progress = Signal(int, int)
    finished = Signal(object)  # InstallOutcome

    def __init__(self, session, worker_base, dest_dir, parent: QObject | None = None):
        super().__init__(parent)
        self._session = session
        self._worker_base = worker_base
        self._dest_dir = Path(dest_dir)
        self._signals = _InstallSignals()
        self._signals.progress.connect(self.progress)
        self._signals.finished.connect(self.finished)
        self._inflight = False

    def install(self, release: dict) -> bool:
        """Begin the staged install. False = rejected up front."""
        if self._inflight:
            return False
        self._inflight = True
        task = _InstallTask(
            release, self._worker_base, self._session, self._dest_dir, self._signals
        )
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(task)
        return True

    def clear(self) -> None:
        """Release the in-flight latch after a finished(...) was consumed."""
        self._inflight = False
