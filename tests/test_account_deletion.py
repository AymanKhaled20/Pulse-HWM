"""Right to erasure: DELETE ACCOUNT removes the cloud account, then the
local session is forgotten."""

from __future__ import annotations

import httpx

from pulse_hwm.cloud.rest import AuthResult, CloudClient
from tests.test_cloud_session import make_manager


def test_client_posts_with_the_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"deleted": True})

    client = CloudClient(
        "https://w.example", "key", transport=httpx.MockTransport(handler)
    )
    assert client.delete_account("acc").ok
    assert seen == {
        "path": "/auth/v1/delete_account",
        "method": "POST",
        "auth": "Bearer acc",
    }


def test_server_refusal_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"msg": "invalid credentials"})

    client = CloudClient(
        "https://w.example", "key", transport=httpx.MockTransport(handler)
    )
    result = client.delete_account("stale")
    assert not result.ok and "invalid credentials" in result.error


def test_successful_delete_forgets_the_local_session():
    mgr, client, store = make_manager()
    mgr.sign_in("e@x.y", "pw")
    client.delete_account = lambda token: AuthResult(ok=True)

    assert mgr.delete_account().ok
    assert not mgr.is_signed_in()
    assert store.refresh == ""  # refresh token wiped from Credential Manager


def test_failed_delete_keeps_the_user_signed_in():
    mgr, client, store = make_manager()
    mgr.sign_in("e@x.y", "pw")
    client.delete_account = lambda token: AuthResult(error="network error")

    assert not mgr.delete_account().ok
    assert mgr.is_signed_in()
    assert store.refresh == "ref"


def test_delete_needs_a_session():
    mgr, _client, _store = make_manager()
    assert "sign in first" in mgr.delete_account().error
