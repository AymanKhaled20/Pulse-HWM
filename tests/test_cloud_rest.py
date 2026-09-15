from __future__ import annotations

import json

import httpx

from pulse_hwm.cloud.rest import CloudClient

BASE = "https://example.supabase.co"
KEY = "publishable-key"


def _client(handler) -> CloudClient:
    transport = httpx.MockTransport(handler)
    return CloudClient(BASE, KEY, transport=transport)


def _auth_json(**extra) -> httpx.Response:
    body = {
        "access_token": "acc",
        "refresh_token": "ref",
        "expires_in": 3600,
        "user": {"id": "user-1", "email": "a@b.c"},
    } | extra
    return httpx.Response(200, json=body)


def test_sign_in_password_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/auth/v1/token"
        assert request.url.params["grant_type"] == "password"
        assert request.headers["apikey"] == KEY
        body = json.loads(request.content)
        assert body["email"] == "a@b.c"
        return _auth_json()

    client = _client(handler)
    result = client.sign_in_with_password("a@b.c", "pw")
    assert result.ok and result.tokens.access_token == "acc"


def test_sign_up_with_confirmation_pending():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("redirect_to")
        return httpx.Response(
            200, json={"user": {"id": "u", "email": "e"}, "access_token": None}
        )

    result = _client(handler).sign_up("e@x.y", "pw", "pulsehwm://auth-callback")
    assert result.ok is False
    assert result.needs_email_confirmation is True
    assert result.tokens is None


def test_error_payloads_become_friendly_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": "bad_request", "error_description": "nope"}
        )

    result = _client(handler).sign_in_with_password("e@x.y", "bad")
    assert result.ok is False
    assert result.error == "bad_request nope"


def test_newer_error_shape_msg_only():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": 400, "msg": "Invalid login"})

    result = _client(handler).sign_in_with_password("e@x.y", "bad")
    assert result.error == "Invalid login"


def _auth_json():
    return httpx.Response(200, json={"access_token": "acc", "refresh_token": "ref2"})


def test_refresh_rotates_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["grant_type"] == "refresh_token"
        assert json.loads(request.content)["refresh_token"] == "old-refresh"
        return _auth_json()

    result = _client(handler).refresh("old-refresh")
    assert result.ok and result.tokens.refresh_token == "ref2"


class _bearer_checker:
    """Asserts the Bearer token rides along on user-scoped calls."""

    def __init__(self, expected):
        self.expected = expected
        self.actual = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.actual = request.headers.get("Authorization")
        return httpx.Response(200, json=[{"user_id": "u", "key": "k"}])


def test_rest_select_carries_user_bearer_and_returns_body():
    checker = _bearer_checker("acc")
    client = _client(checker)
    status, body = client.rest_select("user_settings", "user_id=eq.u", "acc")
    assert status == 200
    assert body[0]["key"] == "k"
    assert checker.actual == "Bearer acc"


# -- updates: GET /updates/latest (Bearer-gated; status 0 = offline) ---


def test_latest_release_sends_bearer_and_version():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/updates/latest"
        assert request.headers["apikey"] == KEY
        captured["auth"] = request.headers.get("authorization", "")
        captured["version"] = request.url.params["version"]
        return httpx.Response(200, json={"latest": "1.2.0", "update_available": True})

    client = _client(handler)
    status, body = client.latest_release("tok-9", "1.1.6")
    assert status == 200 and body["latest"] == "1.2.0"
    assert captured["auth"] == "Bearer tok-9"
    assert captured["version"] == "1.1.6"


def test_latest_release_surfaces_rejections():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"msg": "account inactive"})

    client = _client(handler)
    status, body = client.latest_release("tok", "1.1.6")
    assert status == 403  # checker turns this into a friendly "sign in again"


def test_latest_release_offline_sentinel():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    client = _client(handler)
    status, body = client.latest_release("tok", "1.1.6")
    assert status == 0 and body == {}
