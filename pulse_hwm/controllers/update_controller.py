"""In-app update flow: check results → banner/toast; install → progress.

Moved out of app.py's run() so the state (the release currently on offer)
lives in attributes instead of a mutable dict captured by closures, and so
every path handles a missing ACCOUNT tab the same way. (Before, the install
progress/finish handlers assumed the tab existed and would crash.)
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Slot

from pulse_hwm.cloud.updates.policy import is_newer

log = logging.getLogger("pulse.update")

# gives the "installing…" banner a moment on screen before we exit so the
# silent installer can replace our files (it relaunches Pulse itself)
_QUIT_DELAY_MS = 1500


class UpdateController(QObject):
    def __init__(
        self,
        db,
        alerts,
        checker,
        installer,
        current_version: str,
        account_tab=None,
        show_feedback: Callable[[str], None] = lambda message: None,
        quit_for_update: Callable[[], None] = lambda: None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._alerts = alerts
        self._checker = checker
        self._installer = installer
        self._current_version = current_version
        self._account_tab = account_tab  # None when accounts are unavailable
        self._show_feedback = show_feedback
        self._quit_for_update = quit_for_update
        self._offered_release: dict | None = None  # what INSTALL would install

        checker.checked.connect(self.on_check_done)
        installer.progress.connect(self.on_install_progress)
        installer.finished.connect(self.on_install_finished)
        if account_tab is not None:
            account_tab.install_update_requested.connect(self.on_install_requested)
            account_tab.update_dismissed.connect(self.on_update_dismissed)

    # ── checks ───────────────────────────────────────────────────────────
    @Slot()
    def check_now_manual(self) -> None:
        self._checker.check_now(manual=True)

    @Slot(object)
    def on_check_done(self, outcome) -> None:
        release = dict(outcome.release or {})
        version = str(release.get("version", ""))
        manual = bool(getattr(outcome, "manual", False))
        if outcome.state in ("available", "forced") and version:
            self._on_update_available(
                release, version, forced=outcome.state == "forced"
            )
        elif outcome.state == "skipped":
            if release:
                self._remember_highest_seen(version)  # seen-and-understood
            # silent when periodic; manual checks explain themselves
            if manual and outcome.reason and outcome.reason != "not signed in":
                self._show_feedback(f"updates: {outcome.reason}")
            if self._account_tab is not None:
                self._account_tab.clear_update_banner()
        elif outcome.state == "error":
            # never toast on background check errors; log for diagnostics
            if manual:
                self._show_feedback(f"update check failed: {outcome.reason}")
            log.warning("update check failed: %s", outcome.reason)
            self._db.insert_event(
                time.time(), "ERROR", "update", f"update check failed: {outcome.reason}"
            )

    def _on_update_available(self, release: dict, version: str, forced: bool) -> None:
        self._remember_highest_seen(version)
        # toast + event log only ONCE per version, not on every 6 h check
        if version != self._db.get_setting("update_notified_version", ""):
            self._db.set_setting("update_notified_version", version)
            self._alerts.notify(
                "info",
                "UPDATE AVAILABLE",
                f"Pulse v{version} is ready — you are on v{self._current_version}.",
                play_sound=False,  # info-level: toast only, never a beep
            )
            self._db.insert_event(
                time.time(), "INFO", "update", f"update available: v{version}"
            )
        if self._account_tab is None:
            return  # no ACCOUNT tab → no banner/install button; the toast stands
        self._account_tab.show_update_available(
            release, current_version=self._current_version, forced=forced
        )
        self._offered_release = release

    def _remember_highest_seen(self, version: str) -> None:
        seen = self._db.get_setting("update_highest_seen", "") or ""
        if version and is_newer(version, seen):
            self._db.set_setting("update_highest_seen", version)

    # ── install ──────────────────────────────────────────────────────────
    @Slot()
    def on_install_requested(self) -> None:
        if self._offered_release:
            self._installer.install(self._offered_release)

    @Slot(str)
    def on_update_dismissed(self, version: str) -> None:
        self._db.set_setting("update_dismissed_version", str(version))
        if self._account_tab is not None:
            self._account_tab.clear_update_banner()

    @Slot(int, int)
    def on_install_progress(self, done: int, total: int) -> None:
        if self._account_tab is not None:
            self._account_tab.show_update_progress(int(done), int(total))

    @Slot(object)
    def on_install_finished(self, outcome) -> None:
        self._installer.clear()
        if outcome.ok:
            self._db.insert_event(
                time.time(), "INFO", "update", f"update installing: v{outcome.version}"
            )
            if self._account_tab is not None:
                self._account_tab.show_update_done(outcome.version)
            QTimer.singleShot(_QUIT_DELAY_MS, self._quit_for_update)
        else:
            log.error("update install failed: %s", outcome.error)
            self._db.insert_event(
                time.time(), "ERROR", "update", f"update failed: {outcome.error}"
            )
            if self._account_tab is not None:
                self._account_tab.show_update_error(str(outcome.error))
