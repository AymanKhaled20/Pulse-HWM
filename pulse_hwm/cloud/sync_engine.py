from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from pulse_hwm.app_settings import SYNCABLE_KEYS
from pulse_hwm.cloud.session import SessionManager
from pulse_hwm.cloud.sync import SyncPlan, plan_settings, plan_sites
from pulse_hwm.db import Database

# SyncEngine: automatic two-way merge of settings + sites.
#
#   * pure planning lives in sync.py (unit-tested, no I/O)
#   * THIS module is the executor: fetches cloud rows on a QRunnable,
#     applies the plan with the (thread-safe) Database wrapper, and
#     reports one summary line to the ACCOUNT tab.
#
# Scheduling honesty: instead of hooking every mutation site, a 60 s
# compare-and-merge tick covers "automatic" — the payloads are tiny
# (â‰¤20 keys + â‰¤50 site rows) and merge-shortcircuits to one push/pull.

_TICK_S = 60


def _refresh_if_dead(session: SessionManager) -> bool:
    """One lazy refresh attempt when REST complains about the token."""
    if session.is_signed_in():
        return True
    return session.try_resume()


class SyncWorker(QRunnable):
    """One full compare + push + pull cycle (worker thread)."""

    class _Signals(QObject):
        done = Signal(str, bool, str)  # summary, ok, error

    def __init__(self, session: SessionManager, client, db: Database, signals):
        super().__init__()
        self._session = session
        self._client = client
        self._db = db
        self._signals = signals

    def _fetch(self, table: str, query: str) -> tuple[int, list | dict, str]:
        """GET one table; adds two guards the raw client lacks:

        * 401 → the access token expired since sign-in, so force one
          refresh from the parked refresh token and retry (otherwise
          sync stays broken until app restart).
        * a network blip surfaces as status 0 with a dict body — never
          let that reach the planners, which expect a list.
        """
        token = self._session.bearer()
        status, body = self._client.rest_select(table, query, token)
        if status == 401 and self._session.try_resume(force=True):
            token = self._session.bearer()
            status, body = self._client.rest_select(table, query, token)
        if status == 0 or not isinstance(body, list):
            return -1, [], token
        return status, body, token

    def run(self) -> None:
        try:
            summary, ok, error = self._cycle()
        except Exception as exc:  # a sync bug must never kill anything else
            summary, ok, error = "", False, f"sync crashed: {type(exc).__name__}"
        self._signals.done.emit(summary, ok, error)

    def _cycle(self) -> tuple[str, bool, str]:
        if not _refresh_if_dead(self._session):
            return "not signed in", False, ""
        token = self._session.bearer()
        user_id = self._session.session_info.user_id
        if not token or not user_id:
            return "session unusable", False, "missing token/user"

        status, remote_settings, token = self._fetch(
            "user_settings", "user_id=eq." + user_id
        )
        if status < 0 or status >= 400:
            return "", False, f"cloud unavailable ({status})"
        status, remote_sites, token = self._fetch(
            "user_sites",
            f"user_id=eq.{user_id}&order=updated_at.desc&limit=500",
        )
        if status < 0 or status >= 400:
            return "", False, f"cloud unavailable ({status})"

        local_settings = self._db.get_settings_with_ts()
        local_sites = [dict(r) for r in self._db.get_sites_for_sync()]
        plan: SyncPlan = plan_sites(local_sites, remote_sites or [], user_id)
        settings_plan = plan_settings(
            local_settings, remote_settings or [], user_id, set(SYNCABLE_KEYS)
        )
        plan.push_settings = settings_plan.push_settings
        plan.pull_settings = settings_plan.pull_settings

        if (
            not plan.push_sites
            and not plan.push_settings
            and not plan.pull_settings
            and not plan.pull_sites
            and not plan.pull_site_tombstones
        ):
            return "in sync", True, ""  # nothing moved: skip UI reload choreography

        # —— push remote updates first: pulls applied after land on a
        # server that already knows about our locally-newer rows ——————
        pushed_settings = 0
        if plan.push_settings:
            status, _ = self._client.rest_upsert(
                "user_settings", plan.push_settings, token
            )
            if status >= 400:
                return "", False, f"settings push failed ({status})"
            pushed_settings = len(plan.push_settings)
        if plan.push_sites:
            status, _ = self._client.rest_upsert("user_sites", plan.push_sites, token)
            if status >= 400:
                return "", False, f"sites push failed ({status})"

        # —— apply pulls locally —————————————————————————————————————
        adopted_settings = 0
        for row in plan.pull_settings:
            if self._db.adopt_cloud_setting(row["key"], row["value"], row["ts"]):
                adopted_settings += 1
        adopted_sites = 0
        tombstoned = 0
        for row in plan.pull_sites:
            from pulse_hwm.cloud.sync import _iso_epoch

            if self._db.upsert_synced_site(
                str(row["site_uuid"]),
                str(row.get("name", "")),
                str(row.get("url", "")),
                method=str(row.get("method", "GET")),
                timeout_s=float(row.get("timeout_s", 10.0)),
                expected_status=int(row.get("expected_status", 200)),
                keyword=str(row.get("keyword", "")),
                enabled=bool(row.get("enabled", True)),
                deleted=bool(row.get("deleted", False)),
                updated_at=_iso_epoch(row.get("updated_at", "")),
            ):
                adopted_sites += 1
        for uuid, ts in plan.pull_site_tombstones:
            if self._db.tombstone_site_by_uuid(uuid, ts):
                tombstoned += 1

        summary = (
            f"sync: pushed {len(plan.push_sites)} sites/"
            f"{pushed_settings} settings,"
            f" pulled {adopted_sites} sites/{adopted_settings} settings"
            + (f", removed {tombstoned}" if tombstoned else "")
        )
        return summary, True, ""


class SyncEngine(QObject):
    """QObject face: owns scheduling + signals, schedules workers only."""

    finished = Signal(str, bool, str)  # summary, ok, error

    def __init__(self, session, client, db, parent=None):
        super().__init__(parent)
        self._session = session
        self._client = client
        self._db = db
        self._pool = None
        self._inflight = False

    def attach_pool(self, pool) -> None:
        self._pool = pool

    def sync_now(self) -> None:
        if self._pool is None or self._inflight:
            return
        if not self._session.is_signed_in() and not self._session.session_info.user_id:
            return  # signed out: sync is a no-op, not an error
        self._inflight = True

        signals = SyncWorker._Signals()
        signals.done.connect(self._on_done)
        self._pool.start(SyncWorker(self._session, self._client, self._db, signals))

    def _on_done(self, summary: str, ok: bool, error: str) -> None:
        self._inflight = False
        self.finished.emit(summary, ok, error)
