"""Start and stop the background QThreads in one place.

Replaces four hand-written quit()/wait() pairs in app.py. Shutdown order:
run every "before quit" hook first (e.g. the RGB detach request, which must
reach the worker while its event loop is still running), then ask every
thread to quit, then wait for each with its own timeout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QThread

log = logging.getLogger("pulse.lifecycle")


@dataclass
class _ManagedThread:
    thread: QThread
    wait_ms: int
    before_quit: Callable[[], None] | None


class ThreadGroup:
    def __init__(self) -> None:
        self._threads: list[_ManagedThread] = []

    def add(
        self,
        thread: QThread,
        wait_ms: int = 2500,
        before_quit: Callable[[], None] | None = None,
    ) -> QThread:
        self._threads.append(_ManagedThread(thread, wait_ms, before_quit))
        return thread

    def start_all(self) -> None:
        for managed in self._threads:
            if not managed.thread.isRunning():
                managed.thread.start()

    def shutdown(self) -> None:
        for managed in self._threads:
            if managed.before_quit is not None:
                try:
                    managed.before_quit()
                except Exception:  # one bad hook must not block the rest
                    log.exception("shutdown hook failed")
        # quit ALL first so the threads wind down in parallel, then wait
        for managed in self._threads:
            managed.thread.quit()
        for managed in self._threads:
            if not managed.thread.wait(managed.wait_ms):
                log.warning(
                    "thread %s did not stop within %d ms",
                    managed.thread.objectName(),
                    managed.wait_ms,
                )
