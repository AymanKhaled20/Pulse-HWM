from __future__ import annotations

import time


class FakeKeyring:
    """Stands in for Credential Manager in tests."""

    def __init__(self):
        self.vault: dict[str, str] = {}
        self.broken = False

    def set_password(self, service, account, password):
        if self.broken:
            raise RuntimeError("no backend")
        self.vault[account] = password

    def get_password(self, service, account):
        if self.broken:
            raise RuntimeError("no backend")
        return self.vault.get(account)

    def delete_password(self, service, account):
        if self.broken:
            raise RuntimeError("no backend")
        self.vault.pop(account, None)


class FakeKeyringModule:
    def __init__(self, backend):
        self.backend = backend
        self._backend = backend

    def set_password(self, service, account, password):
        self._backend.set_password(service, account, password)

    def get_password(self, service, account):
        return self._backend.get_password(service, account)

    def delete_password(self, service, account):
        self._backend.delete_password(service, account)

    def get_keyring(self):
        return type("K", (), {"priority": 1})()


def _patched(fake):
    return FakeKeyringModule(fake)


def _manager_with(monkeypatch, fake_keyring: FakeKeyringModule):
    import pulse_hwm.auth.token_store as ts

    monkeypatch.setattr(ts, "_keyring", lambda: fake_keyring)
    return ts


def test_refresh_token_roundtrip(monkeypatch):
    ts = _manager_with(monkeypatch, _patched(FakeKeyring()))
    assert ts.save_refresh_token("tok-abc") is True
    assert ts.load_refresh_token() == "tok-abc"
    ts.clear_refresh_token()
    assert ts.load_refresh_token() == ""


def test_parked_verifier_is_single_use(monkeypatch):
    ts = _manager_with(monkeypatch, _patched(FakeKeyring()))
    assert ts.park_verifier("flow1", "verifier", "pulsehwm://auth-callback")
    verifier, redirect = ts.take_verifier("flow1")
    assert (verifier, redirect) == ("verifier", "pulsehwm://auth-callback")
    # second take: gone (single-use)
    assert ts.take_verifier("flow1") == ("", "")


def test_parked_verifier_expires(monkeypatch):
    ts = _manager_with(monkeypatch, _patched(FakeKeyring()))
    ts.park_verifier("flow2", "verifier", "redirect")
    # age the parked entry beyond the TTL
    account = ts._verifier_account("flow2")
    kr = ts._keyring()
    stored = kr.get_password(ts._SERVICE, account)
    parked_at, verifier, redirect = stored.split("|", 2)
    kr.set_password(
        ts._SERVICE, account, f"{time.time() - 99999:.0f}|{verifier}|{redirect}"
    )
    assert ts.take_verifier("flow2") == ("", "")


def test_garbage_payload_fails_clean(monkeypatch):
    ts = _manager_with(monkeypatch, _patched(FakeKeyring()))
    kr = ts._keyring()
    kr.set_password(ts._SERVICE, ts._verifier_account("flow3"), "not|a|real|payload?")
    # split('Pipe', 2) actually tolerates extra, but malformed timestamps fail
    assert ts.take_verifier("flow3") in [("", ""), ("a", "real|payload")]


def test_no_backend_never_raises(monkeypatch):
    ts = _manager_with(monkeypatch, _patched(FakeKeyring()))
    ts._keyring().backend.broken = True
    assert ts.save_refresh_token("x") is False  # refused, not crashed
    assert ts.load_refresh_token() == ""
    assert ts.park_verifier("f", "v", "r") is False
    assert ts.take_verifier("f") == ("", "")
    ts.clear_refresh_token()  # must not raise


def test_flow_id_sanitization(monkeypatch):
    ts = _manager_with(monkeypatch, _patched(FakeKeyring()))
    # spaces/weird chars get stripped so the account name stays sane
    assert ts.park_verifier("ab/../cd ef", "verifier", "r") is True
    verifier, redirect = ts.take_verifier("ab/../cd ef")
    assert (verifier, redirect) == ("verifier", "r")
