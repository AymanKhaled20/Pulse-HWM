from __future__ import annotations

from pulse_hwm.auth.session import SessionManager


class FakeStore:
    """Keyring stand-in: records what was parked/rotated/cleared."""

    def __init__(self):
        self.refresh = ""
        self.parked: dict[str, tuple[str, str]] = {}
        self.save_calls = 0

    # refresh token
    def save_refresh_token(self, token: str) -> bool:
        self.refresh = token
        self.save_calls += 1
        return True

    def load_refresh_token(self) -> str:
        return self.refresh

    def clear_refresh_token(self) -> None:
        self.refresh = ""

    # pkce parking
    def park_verifier(self, flow_id: str, verifier: str, redirect: str) -> bool:
        self.parked[flow_id] = (verifier, redirect)
        return True

    def take_verifier(self, flow_id: str) -> tuple[str, str]:
        return self.parked.pop(flow_id, ("", ""))

    def clear_pending_flow(self, flow_id: str) -> None:
        self.parked.pop(flow_id, None)


class FakeClient:
    """Auth endpoints stubbed; records what it was asked to do."""

    def __init__(self):
        self.refresh_calls: list[str] = []
        self.logout_calls: list[str] = []

    def sign_in_with_password(self, email, password):
        from pulse_hwm.auth.rest import AuthResult

        return AuthResult(
            ok=True,
            tokens=TOK,
            user={"id": "u-1", "email": email},
        )

    def refresh(self, token: str):
        from pulse_hwm.auth.rest import AuthResult

        self.refresh_calls.append(token)
        # rotate the refresh token each time, like Supabase does
        return AuthResult(
            ok=True,
            tokens=TOK.__class__("acc-2", token + "-rotated"),
            user={"id": "u-1", "email": "e@x.y"},
        )

    def logout(self, access_token: str) -> None:
        self.logout_calls.append(access_token)

    def exchange_pkce_code(self, code: str, verifier: str):
        from pulse_hwm.auth.rest import AuthResult

        assert code == "the-code"
        self.verifier_seen = verifier
        return AuthResult(ok=True, tokens=TOK, user={"id": "u-1", "email": "e@x.y"})


from pulse_hwm.auth.rest import Tokens  # noqa: E402 — placed here for clarity

TOK = Tokens(access_token="acc", refresh_token="ref")


def make_manager():
    store = FakeStore()
    client = FakeClient()
    return SessionManager(client, store), client, store


def test_password_sign_in_parks_refresh_and_sets_session():
    mgr, client, store = make_manager()
    result = mgr.sign_in("e@x.y", "pw")
    assert result.ok
    assert mgr.is_signed_in()
    assert mgr.session_info.user_id == "u-1"
    assert mgr.session_info.provider == "password"
    assert store.refresh == "ref"  # crown jewel parked in the store


def test_resume_uses_and_rotates_stored_token():
    mgr, client, store = make_manager()
    store.refresh = "old-token"
    assert mgr.try_resume() is True
    assert client.refresh_calls == ["old-token-rotated"[:0] or "old-token"] or True
    assert client.refresh_calls == ["old-token"]
    assert store.refresh == "old-token-rotated"  # rotation parked instantly


def test_resume_with_dead_token_clears_session():
    mgr, client, store = make_manager()
    store.refresh = "dead"

    class DeadClient(FakeClient):
        def refresh(self, token):
            from pulse_hwm.auth.rest import AuthResult

            return AuthResult(error="session expired")

    mgr._client = DeadClient()
    assert mgr.try_resume() is False
    assert mgr.is_signed_in() is False
    assert store.refresh == ""  # dead token forgotten


def test_resume_network_error_keeps_refresh_token():
    """Offline 'resume' must NOT forget the parked token — otherwise one
    offline launch signs the user out permanently."""
    from pulse_hwm.auth.rest import AuthResult

    mgr, client, store = make_manager()
    store.refresh = "parked"

    class OfflineClient(FakeClient):
        def refresh(self, token):
            return AuthResult(
                error="network error refreshing the session", network_error=True
            )

    mgr._client = OfflineClient()
    assert mgr.try_resume() is False
    assert store.refresh == "parked"  # kept for the next tick/launch
    assert mgr.is_signed_in() is False


def test_pkce_flow_journal():
    mgr, client, store = make_manager()
    store.park_verifier("flow123", "verifier-abc", "pulsehwm://auth-callback")
    result = mgr.adopt_pkce_result("the-code", "flow123", provider="google")
    assert result.ok
    assert client.verifier_seen == "verifier-abc"
    assert mgr.session_info.provider == "google"
    # verifier was single-use
    assert store.parked == {}


def test_pkce_with_missing_flow_fails_cleanly():
    mgr, client, store = make_manager()
    result = mgr.adopt_pkce_result("code", "missing", provider="github")
    assert result.ok is False
    assert "expired" in result.error
    assert mgr.is_signed_in() is False


def test_sign_out_revokes_and_clears():
    mgr, client, store = make_manager()
    mgr.sign_in("e@x.y", "pw")
    mgr.sign_out()
    assert client.logout_calls == ["acc"]
    assert store.refresh == ""
    assert mgr.is_signed_in() is False
    assert mgr.tokens is None
