from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from pulse_hwm.processes import (
    CATEGORY_LABELS,
    ProcessScanner,
    cap_rows,
    group_processes,
)


class ProcessesCollector(QObject):
    """Scans all processes on its worker thread and emits grouped snapshots.

    Resource rules this lives by:
      * the scan timer only runs while the Processes tab is visible
        (`set_active(False)` pauses it, which the UI toggles);
      * scans emit plain dicts (rows as dicts, not ProcessRow) so nothing
        thread-unsafe crosses into the UI thread;
      * the scanner evicts dead-PID caches every scan, so memory stays loose.
    """

    updated = Signal(dict)

    def __init__(
        self,
        interval_s: float = 3.0,
        max_rows: int = 400,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.interval_s = max(1.0, float(interval_s))
        self.max_rows = max(50, int(max_rows))
        self._scanner = ProcessScanner()
        self._active = True
        self._timer: QTimer | None = None

    # ── lifecycle (called on the worker thread) ────────────────────────────
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(self.interval_s * 1000))
        self._tick()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def set_active(self, active: bool) -> None:
        """Pause/resume the scan without killing the worker thread."""
        if self._active == bool(active):
            return
        self._active = bool(active)
        if self._timer is not None:
            if self._active:
                self._tick()  # stale data while paused: refresh immediately
                self._timer.start(int(self.interval_s * 1000))
            else:
                self._timer.stop()

    def reconfigure(
        self, interval_s: float | None = None, max_rows: int | None = None
    ) -> None:
        if interval_s is not None:
            self.interval_s = max(1.0, float(interval_s))
        if max_rows is not None:
            self.max_rows = max(50, int(max_rows))
        if self._timer is not None and self._active:
            self._timer.start(int(self.interval_s * 1000))

    def _tick(self) -> None:
        if not self._active:
            return
        snap = self.snapshot()
        self.updated.emit(snap)

    def refresh_now(self) -> None:
        """Slot: run an immediate scan (queued from the UI thread)."""
        if self._active:
            self._tick()

    def snapshot(self) -> dict:
        """One scan → grouped payload. Plain dicts so it is thread-safe."""
        rows = self._scanner.scan()
        own = self._scanner.self_pid
        capped = cap_rows(rows, self.max_rows, self_pid=own)
        groups = group_processes(capped)
        return {
            "ts": time.time(),
            "self_pid": own,
            "total": len(rows),
            "shown": len(capped),
            "error": self._scanner.last_error,
            "labels": CATEGORY_LABELS,
            # rows are converted to plain dicts for the cross-thread hop
            "groups": {
                cat: [self._row_to_dict(r) for r in rows_in_cat]
                for cat, rows_in_cat in groups.items()
            },
        }

    @staticmethod
    def _row_to_dict(r) -> dict:
        return {
            "pid": r.pid,
            "name": r.name,
            "cpu": r.cpu,
            "mem_pct": r.mem_pct,
            "mem_rss": r.mem_rss,
            "username": r.username,
            "status": r.status,
            "exe": r.exe,
            "category": r.category,
        }


class ProcessesThreadBridge:
    """Runs ProcessesCollector on a background thread (same pattern as the
    hardware / websites bridges)."""

    @staticmethod
    def attach(
        thread, interval_s: float = 3.0, max_rows: int = 400
    ) -> ProcessesCollector:
        collector = ProcessesCollector(interval_s=interval_s, max_rows=max_rows)
        collector.moveToThread(thread)
        thread.started.connect(collector.start)
        thread.finished.connect(collector.stop)
        return collector
