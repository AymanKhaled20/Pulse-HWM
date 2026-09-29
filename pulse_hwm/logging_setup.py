"""One logging setup for the whole app.

Before this module: an ad-hoc rgb_debug.log (opened + appended by hand on
every tick), a hand-written error.log in the excepthook, and a few print()s.
Now every module just does `log = logging.getLogger("pulse.<area>")` and
the handlers below decide where lines go:

  * %LOCALAPPDATA%\\PulseHWM\\pulse.log  — everything at LOG_LEVEL and up,
    rotated (1 MB × 3) so it can never grow without bound;
  * %LOCALAPPDATA%\\PulseHWM\\error.log  — ERROR and up only, so crashes
    stay easy to find (same file name the old excepthook used).

Level comes from the PULSE_LOG_LEVEL env var (.env), default INFO. Set it
to DEBUG when chasing a bug to also get per-second engine chatter.
Never log secret values (tokens, webhook URLs, passwords).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(threadName)s] %(message)s"
_MAX_BYTES = 1_000_000
_BACKUPS = 3
_configured = False


def configure_logging(log_dir: Path) -> None:
    """Attach the file handlers + crash hooks. Safe to call more than once
    (later calls are no-ops), so tests and --selftest can't double-log."""
    global _configured
    if _configured:
        return
    _configured = True

    level_name = os.environ.get("PULSE_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(_FORMAT)

    root = logging.getLogger("pulse")
    root.setLevel(level)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        main_handler = RotatingFileHandler(
            log_dir / "pulse.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUPS,
            encoding="utf-8",
        )
        main_handler.setFormatter(formatter)
        root.addHandler(main_handler)

        error_handler = RotatingFileHandler(
            log_dir / "error.log",
            maxBytes=_MAX_BYTES,
            backupCount=1,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root.addHandler(error_handler)
    except OSError:
        # read-only / locked data dir: the app must still run, just unlogged
        pass

    # still echo to the console in dev runs (python -m pulse_hwm)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    _install_crash_hooks()


def _install_crash_hooks() -> None:
    """Route uncaught exceptions (main thread, Qt slots, Python threads)
    into the log. PySide6 reports exceptions raised inside slots through
    sys.excepthook, so this also catches UI callback crashes."""
    crash_log = logging.getLogger("pulse.crash")

    def excepthook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        crash_log.error("uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        crash_log.error(
            "uncaught exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
