"""One scheduler for all background jobs — no more ad-hoc QTimer sprawl.

Before this module existed in app.py: sync (60s), memory trim (15m), DB
prune (24h), update checks (6h) — four independent QTimers with no jitter,
no backoff, no single place to observe. Jobs here share one ticker:

  * jitter spreads bursts (a fleet of installs must not hammer the worker
    at the same wall-clock moment);
  * a job that raises gets next ticks doubled (capped) instead of silently
    spamming every period forever;
  * outcomes emitted on job_event; persistence/logging stays in callers
    (this module is UI-pure: it decides WHEN, never does the work).

Every callback runs on the UI thread and must hand real work off to a
worker (QThreadPool / ThreadBridge).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

_TICK_MS = 250
_JITTER_PCT = 8.0
_BACKOFF_MAX = 4  # cap consecutive error backoff at 2**4 = 16x


@dataclass
class _Job:
    name: str
    interval_ms: int
    callback: Callable[[], None]
    immediate: bool = True
    next_run_monotonic: float = 0.0
    consecutive_errors: int = 0


class Scheduler(QObject):
    """Single drift-free ticker; jobs fire when their slot comes due."""

    job_event = Signal(str, str)  # name, "ran" | "error: <why>"

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._jobs: dict[str, _Job] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._running = False

    # —— wiring —————————————————————————————————————————————————————————
    def add_job(
        self,
        name: str,
        interval_ms: int,
        callback: Callable[[], None],
        immediate: bool = True,
    ) -> None:
        job = _Job(
            name=name,
            interval_ms=max(int(interval_ms), _TICK_MS),
            callback=callback,
            immediate=immediate,
        )
        self._jobs[job.name] = job
        self._reschedule(job, first=True)

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    def trigger(self, name: str) -> bool:
        """Force a job's next fire on the very next tick (tray actions)."""
        job = self._jobs.get(name)
        if job is None:
            return False
        job.next_run_monotonic = 0.0
        return True

    def next_run_in_s(self, name: str) -> float:
        job = self._jobs.get(name)
        if job is None:
            return -1.0
        return max(0.0, job.next_run_monotonic - time.monotonic())

    # —— engine ——————————————————————————————————————————————————————————
    def _reschedule(self, job: _Job, first: bool) -> None:
        if first and job.immediate:
            delay = 0.0  # due on the very next tick (~250 ms), not a period in
        else:
            delay = job.interval_ms
            if not first and job.consecutive_errors:
                delay = delay * min(2**job.consecutive_errors, 2**_BACKOFF_MAX)
            delay = int(
                delay * (1.0 + random.uniform(-_JITTER_PCT, _JITTER_PCT) / 100.0)
            )
            delay = max(delay, _TICK_MS * 2)
        job.next_run_monotonic = time.monotonic() + delay / 1000.0

    def _tick(self) -> None:
        now = time.monotonic()
        for job in [j for j in self._jobs.values() if j.next_run_monotonic <= now]:
            error: str | None = None
            try:
                job.callback()
            except Exception as exc:  # a bad job must not stop the ticker
                error = f"{type(exc).__name__}: {exc}"
            if error and job.consecutive_errors < _BACKOFF_MAX:
                job.consecutive_errors += 1
            elif not error:
                job.consecutive_errors = 0
            self._reschedule(job, first=False)
            self.job_event.emit(job.name, f"error: {error}" if error else "ran")
