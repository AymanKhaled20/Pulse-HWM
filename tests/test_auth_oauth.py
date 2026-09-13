from __future__ import annotations

import pytest

from pulse_hwm.auth import pkce
from pulse_hwm.auth.oauth import (
    REDIRECT_URI,
    OauthCoordinator,
    parse_callback_url,
)


class FakeStore:
    def __init__(self):
        self.parked: dict[str, tuple[str, str]] = {}
        self.broken = False
        self.cleared: set[str] = set()

    def park_verifier(self, flow_id, verifier, redirect):
        if self.broken:
            return False
        self.parked[flow_id] = (verifier, redirect)
        return True

    def take_verifier(self, flow_id):
        return self.parked.pop(flow_id, ("", ""))

    def clear_pending_flow(self, flow_id):
        self.cleared.add(flow_id)
        self.parked.pop(flow_id, None)

    def save_pending_flow(self, flow_id, provider):
        self.pending_flow = (flow_id, provider)
        return True

    def load_pending_flow(self):
        return getattr(self, "pending_flow", ("", ""))

    def clear_pending_flow_id(self):
        self.pending_flow = ("", "")


class FakeClient:
    def oauth_authorize_url(self, provider, redirect_to, challenge):
        return f"https://supa/auth/v1/authorize?provider={provider}&redirect_to={redirect_to}&code_challenge={challenge}"


def make_coordinator():
    store = FakeStore()
    return OauthCoordinator(FakeClient(), store), store


def test_start_parks_verifier_and_builds_url():
    coord, store = make_coordinator()
    url = coord.start("google")
    assert coord.pending is not None
    assert url.startswith("https://supa/auth/v1/authorize?provider=google")
    assert REDIRECT_URI in url
    verifier, redirect = list(store.parked.values())[0]
    assert len(verifier) == 64
    assert redirect == REDIRECT_URI
    # challenge in the URL matches the parked verifier (PKCE's whole point)
    challenge = url.split("code_challenge=")[1]
    assert challenge == pkce.code_challenge(verifier)


def test_start_requires_secure_store():
    coord, store = make_coordinator()
    store.broken = True
    with pytest.raises(RuntimeError):
        coord.start("github")


def test_reject_pending_clears_verifier():
    coord, store = make_coordinator()
    coord.start("google")
    flow_id = coord.pending.flow_id
    coord.reject_pending()
    assert flow_id in store.cleared
    assert coord.pending is None


def test_parse_callback_success():
    result = parse_callback_url("pulsehwm://auth-callback?code=abc123&state=whatever")
    assert result.ok is True
    assert result.code == "abc123"
    assert result.error == ""


def test_parse_callback_provider_error():
    result = parse_callback_url(
        "pulsehwm://auth-callback?error=access_denied&error_description=User+denied"
    )
    assert result.ok is False
    assert result.error == "User denied"


def test_parse_callback_missing_code():
    result = parse_callback_url("pulsehwm://auth-callback?nonsense=1")
    assert result.ok is False
    assert "code" in result.error


def test_parse_callback_rejects_other_schemes():
    assert parse_callback_url("https://evil.example?code=stolen").ok is False
    assert parse_callback_url("").ok is False
    assert parse_callback_url("pulsehwm://").ok is False


def test_cold_launch_restores_pending_flow():
    coord, store = make_coordinator()
    coord.start_email_flow("signup")  # user clicked create account, app died
    later = OauthCoordinator(FakeClient(), store)  # NEW process object
    later.restore_pending()
    assert later.pending is not None
    assert later.pending.provider == "signup"
    later.clear_pending()
    assert OauthCoordinator(FakeClient(), store).pending is None  # pointer gone
