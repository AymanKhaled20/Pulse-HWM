from __future__ import annotations

import base64
import json

from pulse_hwm.cloud.updates import trust
from pulse_hwm.cloud.updates.checker import UpdateChecker, _CheckSignals, _CheckTask
from pulse_hwm.db import Database

WORKER = "https://pulsehwm-cloud.pulsehwm27.workers.dev"
VERSION = "1.2.0"
ASSET_NAME = "PulseHWM-Setup-1.2.0.exe"


def _signed_payload(version: str = VERSION, overwrite: dict | None = None):
    """A real Ed25519-signed /updates/latest payload (+ its public key)."""
    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    manifest = json.dumps(
        {
            "version": version,
            "asset_name": f"PulseHWM-Setup-{version}.exe",
            "sha256": "a" * 64,
        },
        separators=(",", ":"),
    )
    sig = (
        base64.urlsafe_b64encode(sk.sign(manifest.encode("utf-8")).signature)
        .decode("ascii")
        .rstrip("=")
    )
    payload = {
        "latest": version,
        "manifest": manifest,
        "manifest_sig": sig,
        "notes": "big release",
        "published_at": "2026-09-20T00:00:00+00:00",
        "min_supported": "",
        "mandatory": False,
        "download_url": f"/dl/PulseHWM-Setup-{version}.exe",
        "fallback_url": f"https://github.com/a/b/releases/download/v{version}/{version}.exe",
    }
    if overwrite:
        payload.update(overwrite)
    return payload, sk.verify_key.encode().hex()


class FakeSession:
    def __init__(self, signed_in: bool = True):
        self._signed_in = signed_in
        self._token = "tok-1"
        self.token_after_refresh = "tok-2"
        self.force_refresh_calls = 0

    def is_signed_in(self):
        return self._signed_in

    def bearer(self):
        return self._token

    def try_resume(self, force=False):
        self.force_refresh_calls += 1
        self._token = self.token_after_refresh
        return True


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def latest_release(self, token, current_version):
        self.calls.append((token, current_version))
        return self._responses.pop(0)


def _task(monkeypatch, tmp_path, responses, pub_hex=None, session=None) -> object:
    """One checker cycle against scripted responses (no threads involved)."""
    session = session or FakeSession()
    client = FakeClient(responses)
    monkeypatch.setattr(trust, "TRUSTED_UPDATE_KEYS", frozenset({pub_hex or "0" * 64}))
    task = _CheckTask(
        session,
        client,
        Database(tmp_path / "s.db"),
        "1.1.6",
        WORKER,
        _CheckSignals(),
        False,
    )
    return task._cycle()


def test_offline_is_not_an_error(monkeypatch, tmp_path):
    outcome = _task(monkeypatch, tmp_path, [(0, {})], pub_hex=None)
    assert outcome.state == "none" and outcome.reason == "offline"


def test_401_refreshes_once_then_trusts_the_new_token(monkeypatch, tmp_path):
    payload, pub = _signed_payload()
    session = FakeSession()
    client = FakeClient([(401, {}), (200, payload)])
    monkeypatch.setattr(trust, "TRUSTED_UPDATE_KEYS", frozenset({pub}))
    task = _CheckTask(
        session,
        client,
        Database(tmp_path / "a.db"),
        "1.1.6",
        WORKER,
        _CheckSignals(),
        False,
    )
    outcome = task._cycle()
    assert outcome.state == "available", outcome.reason
    assert session.force_refresh_calls == 1
    assert client.calls[0][0] == "tok-1"  # first attempt: stale token
    assert client.calls[1][0] == session.token_after_refresh  # retry: fresh


def test_inactive_account_gets_friendly_skip(monkeypatch, tmp_path):
    outcome = _task(monkeypatch, tmp_path, [(403, {})], None)
    assert outcome.state == "skipped" and "sign in again" in outcome.reason


def test_bad_signature_is_refused(monkeypatch, tmp_path):
    payload, pub = _signed_payload()
    # provision a DIFFERENT key → the trusted set rejects the signature
    outcome = _task(monkeypatch, tmp_path, [(200, payload)], "f" * 64)
    assert outcome.state == "error" and "signature" in outcome.reason


def test_server_disagreement_with_manifest_is_refused(monkeypatch, tmp_path):
    payload, pub = _signed_payload(overwrite={"latest": "9.9.9"})
    outcome = _task(monkeypatch, tmp_path, [(200, payload)], pub)
    assert outcome.state == "error" and "disagrees" in outcome.reason


def test_available_offer_is_signed_and_carried(monkeypatch, tmp_path):
    payload, pub = _signed_payload()
    outcome = _task(monkeypatch, tmp_path, [(200, payload)], pub)
    assert outcome.state == "available"
    assert outcome.release["version"] == VERSION
    assert outcome.release["manifest"]  # used for install-time re-verify
    assert outcome.release["download_url"].endswith(f"/dl/{ASSET_NAME}")


def test_dismissed_version_stays_skipped(monkeypatch, tmp_path):
    payload, pub = _signed_payload()
    db = Database(tmp_path / "d.db")
    db.set_setting("update_dismissed_version", VERSION)
    monkeypatch.setattr(trust, "TRUSTED_UPDATE_KEYS", frozenset({pub}))
    session = FakeSession()
    client = FakeClient([(200, payload)])
    task = _CheckTask(session, client, db, "1.1.6", WORKER, _CheckSignals(), False)
    outcome = task._cycle()
    assert outcome.state == "skipped"


def test_signed_out_skips_before_the_network(tmp_path):
    # UpdateChecker early-out: never reach the pool/cloud while signed out
    session = FakeSession(signed_in=False)
    client = FakeClient([])

    checker = UpdateChecker(
        session, client, Database(tmp_path / "e.db"), "1.1.6", WORKER
    )
    seen = []
    checker.checked.connect(seen.append)
    assert checker.check_now(manual=True) is False  # declined to schedule
    assert seen and seen[0].state == "skipped"
    assert client.calls == []  # no round-trip spent for a non-member
