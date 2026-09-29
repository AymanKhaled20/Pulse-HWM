"""Accounts + cloud sync glue: login-link callbacks, silent session resume,
and applying what a sync pulled down (theme, settings form, site list).

Moved out of app.py's run(): the "was I signed in last time?" flag is now
an attribute instead of a {"v": ...} dict captured by a closure, and the
signed-in hook no longer references objects defined later in run().
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, Slot

from pulse_hwm import app_settings
from pulse_hwm.cloud.oauth import parse_callback_url

log = logging.getLogger("pulse.sync")

# sync summaries meaning "nothing changed locally" — reapplying settings on
# these would clobber a half-edited Settings form every 60 s
_NO_CHANGE_SUMMARIES = ("not signed in", "in sync")


class SyncController(QObject):
    def __init__(
        self,
        db,
        session,
        coordinator,
        sync_engine,
        window,
        theme_manager,
        websites_monitor,
        on_signed_in: Callable[[], None] = lambda: None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._session = session
        self._coordinator = coordinator
        self._sync_engine = sync_engine
        self._window = window
        self._theme_manager = theme_manager
        self._monitor = websites_monitor
        self._on_signed_in = on_signed_in  # e.g. kick an update check
        self._was_signed_in = session.is_signed_in()

        sync_engine.finished.connect(self.on_sync_finished)
        account_tab = window.account_tab
        if account_tab is not None:
            account_tab.sync_requested.connect(sync_engine.sync_now)

    # ── sign-in paths ────────────────────────────────────────────────────
    @Slot(str)
    def handle_incoming_url(self, url: str) -> None:
        """Adopt an authentication callback (pulsehwm:// link) — forwarded
        by a relaunched instance, or passed on our own command line."""
        # the user is still sitting in the browser — surface the window
        self._window.bring_to_front()
        callback = parse_callback_url(url)
        if not callback.ok:
            # dead flow: drop the parked verifier so no later callback can
            # restore it, and release the buttons for a clean retry
            self._coordinator.reject_pending()
            self._window.show_account_feedback(f"login link problem: {callback.error}")
            return
        # cold launch: this process never issued the sign-in, but the
        # verifier is parked in Credential Manager — rebuild the flow
        self._coordinator.restore_pending()
        pending = self._coordinator.pending
        if pending is None:
            self._window.show_account_feedback("no sign-in in progress — link expired")
            return
        result = self._session.adopt_pkce_result(
            callback.code, pending.flow_id, pending.provider
        )
        self._coordinator.clear_pending()  # verifier consumed (single-use)
        self._window.on_oauth_result(result.ok, result.error)
        if result.ok:
            self.session_started()

    @Slot()
    def resume_session(self) -> None:
        """Silent session restore from Credential Manager on boot."""
        if self._session.try_resume():
            self._window.on_oauth_result(True, "")

    def session_started(self) -> None:
        # sign-in (and OAuth adoption) both kick a sync immediately, plus
        # an immediate update check — the daily-use contract for members
        self._sync_engine.sync_now()
        self._on_signed_in()

    # ── sync results ─────────────────────────────────────────────────────
    @Slot(str, bool, str)
    def on_sync_finished(self, summary: str, ok: bool, error: str) -> None:
        self._window.show_account_feedback(summary or error)
        # only an actual SIGN-OUT (signed in → signed out because the parked
        # token was rejected) should repaint; showing "session expired" on a
        # never-signed-in install every 60 s would be noise
        signed_in = self._session.is_signed_in()
        if self._was_signed_in and not signed_in:
            self._window.on_oauth_result(False, "session expired — sign in again")
        self._was_signed_in = signed_in

        if ok and summary and summary not in _NO_CHANGE_SUMMARIES:
            self._apply_pulled_changes()

    def _apply_pulled_changes(self) -> None:
        """The cloud may have updated syncable settings and sites."""
        merged = app_settings.load(self._db)
        self._theme_manager.apply(merged.theme_color, merged.theme_font, persist=False)
        self._theme_manager.set_body_px(merged.font_size)
        # push merged values into the Settings form so the next SAVE can't
        # clobber cloud-newer rows with stale spinbox values
        settings_tab = self._window.settings_tab
        if settings_tab is not None:
            settings_tab.load_from(merged)
        self._monitor.reconfigure(
            interval_s=merged.website_interval_s,
            timeout_s=merged.website_timeout_s,
            ssl_warn_days=merged.ssl_warn_days,
        )
        # pulled sites (adds AND tombstones) must repaint the WEBSITES list
        sites_tab = self._window.sites_tab
        if sites_tab is not None:
            sites_tab.reload_sites()
