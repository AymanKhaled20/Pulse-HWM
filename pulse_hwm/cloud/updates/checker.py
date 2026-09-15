"""Update checker bridge — QThreadPool task polling /updates/latest.

Mirrors SyncEngine conventions (no private QThread; blocking network work
in a QRunnable; results cross threads by Signals). The worker touches
SessionManager through the same lazy-refresh pattern SyncEngine already
uses on 401, so there is no cross-thread Qt state race here either.

Integrity pipeline inside the task:
  1. fetch → 401 → ONE forced session refresh → fetch again (SyncEngine rule)
  2. verify the Ed25519 manifest signature (trust.py — fail-closed)
  3. bind top-level fields to the SIGNED manifest (server fields may lie)
  4. policy.evaluate(): newer? anti-rollback? host allowlist? mandatory?

Outcomes cross to the UI thread as CheckOutcome; the caller decides about
toast/banner specifics; this module stays a thin observer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QRunnable, Signal

from pulse_hwm.cloud.updates import policy, trust


@dataclass
class CheckOutcome:
    """One check cycle, consumed on the UI thread."""

    state: str = "none"  # "none" | "available" | "forced" | "skipped" | "error"
    reason: str = ""
    release: dict = field(default_factory=dict)  # version/notes/download_url/...
    manual: bool = False


class _CheckSignals(QObject):
    done = Signal(object)  # CheckOutcome


class _CheckTask(QRunnable):
    def __init__(
        self,
        session,
        client,
        db,
        current_version: str,
        worker_base: str,
        signals: _CheckSignals,
        manual: bool,
    ):
        super().__init__()
        self._session = session
        self._client = client
        self._db = db
        self._version = current_version
        self._worker_base = worker_base
        self._signals = signals
        self._manual = manual

    # ── pool thread — never raise out of run() ────────────────────────
    def run(self) -> None:
        try:
            outcome = self._cycle()
        except Exception as exc:  # defensive: pool workers must not die silent
            outcome = CheckOutcome(
                state="error", reason=f"unexpected: {exc}", manual=self._manual
            )
        self._signals.done.emit(outcome)

    def _cycle(self) -> CheckOutcome:
        token = self._session.bearer()
        status, payload = self._client.latest_release(token, self._version)
        # expired access tokens get exactly ONE lazy refresh + retry, the
        # same rule SyncEngine uses — never a refresh storm
        if status == 401 and self._session.try_resume(force=True):
            status, payload = self._client.latest_release(
                self._session.bearer(), self._version
            )
        if status == 0:
            return CheckOutcome(state="none", reason="offline", manual=self._manual)
        if status in (401, 403):
            # 403 = account outside the activity window: the server refuses
            # updates — surface a clear "sign in again" state, no generic error
            return CheckOutcome(
                state="skipped",
                reason="account inactive — sign in again to get updates",
                manual=self._manual,
            )
        if status >= 400:
            return CheckOutcome(
                state="error",
                reason=f"update server error ({status})",
                manual=self._manual,
            )
        if not isinstance(payload, dict):
            payload = {}

        # 1) Ed25519 over the EXACT manifest string (fail-closed)
        manifest = str(payload.get("manifest", "") or "")
        sig = str(payload.get("manifest_sig", "") or "")
        if not trust.manifest_signature_ok(manifest, sig):
            return CheckOutcome(
                state="error",
                reason="update signature missing or invalid",
                manual=self._manual,
            )
        signed = policy.parse_manifest(manifest) or {}

        # 2) the unsigned top fields may only DECORATE the signed facts —
        #    a disagreement means misconfigured or malicious server
        if str(payload.get("latest") or "") != str(signed.get("version") or ""):
            return CheckOutcome(
                state="error",
                reason="server disagrees with the signed manifest",
                manual=self._manual,
            )

        # 3) decisions use only signed facts (urls/notes decorate only)
        highest_seen = self._db.get_setting("update_highest_seen", "")
        dismissed = self._db.get_setting("update_dismissed_version", "")

        release = dict(signed)
        release.update(
            {
                "manifest": manifest,
                "manifest_sig": sig,
                "notes": str(payload.get("notes", "") or ""),
                "html_url": str(payload.get("html_url", "") or ""),
                "published_at": str(payload.get("published_at", "") or ""),
                "min_supported": str(payload.get("min_supported", "") or ""),
                "mandatory": bool(payload.get("mandatory")),
                "download_url": str(payload.get("download_url", "") or ""),
                "fallback_url": str(payload.get("fallback_url", "") or ""),
            }
        )
        decision = policy.evaluate(
            release,
            self._version,
            highest_seen,
            self._worker_base,
            {policy.host_of(self._worker_base.rstrip("/"))},
            dismissed_version=dismissed,
        )
        return CheckOutcome(
            state=decision.state,
            reason=decision.reason,
            release=release if decision.state != "skipped" else release,
            manual=self._manual,
        )


class UpdateChecker(QObject):
    """Client-side face for update checks; schedule via check_now()."""

    checked = Signal(object)  # CheckOutcome

    def __init__(
        self,
        session,
        client,
        db,
        current_version: str,
        worker_base: str,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._session = session
        self._client = client
        self._db = db
        self._version = current_version
        self._worker_base = worker_base
        self._signals = _CheckSignals()
        self._signals.done.connect(self._on_done)
        self._inflight = False

    def check_now(self, manual: bool = False) -> bool:
        """Schedule a check. False = skipped (already running / signed out).

        Cheap early-outs BEFORE the pool: a signed-out user doesn't spend
        a network round-trip asking for updates they can't receive.
        """
        if self._inflight:
            return False
        if not self._session.is_signed_in():
            self._signals.done.emit(
                CheckOutcome(state="skipped", reason="not signed in", manual=manual)
            )
            return False
        self._inflight = True
        self._start_task(manual)
        return True

    def _start_task(self, manual: bool) -> None:
        task = _CheckTask(
            self._session,
            self._client,
            self._db,
            self._version,
            self._worker_base,
            self._signals,
            manual,
        )
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(task)

    def _on_done(self, outcome: object) -> None:
        self._inflight = False
        self.checked.emit(outcome)
