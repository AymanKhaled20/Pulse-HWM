from __future__ import annotations

import httpx
import pytest

from pulse_hwm.collectors.websites import check_site, ssl_expiry_days


def make_site(**overrides) -> dict:
    site = {
        "id": 1, "name": "Test", "url": "https://example.test/", "method": "GET",
        "timeout_s": 5.0, "expected_status": 200, "keyword": "",
    }
    site.update(overrides)
    return site


def client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ok_200():
    c = client_with(lambda req: httpx.Response(200, text="hello"))
    result = check_site(make_site(), client=c)
    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["latency_ms"] >= 0
    assert result["error"] == ""


def test_wrong_status_fails():
    c = client_with(lambda req: httpx.Response(503, text=""))
    result = check_site(make_site(expected_status=200), client=c)
    assert result["ok"] is False


def test_expected_status_match():
    c = client_with(lambda req: httpx.Response(503, text="maintenance"))
    result = check_site(make_site(expected_status=503), client=c)
    assert result["ok"] is True


def test_keyword_match():
    c = client_with(lambda req: httpx.Response(200, text="welcome to the dashboard"))
    ok = check_site(make_site(keyword="DASHBOARD"), client=c)
    miss = check_site(make_site(keyword="nonexistent"), client=c)
    assert ok["ok"] is True
    assert miss["ok"] is False
    assert "keyword" in miss["error"]


def test_timeout_error():
    def slow_handler(req):
        raise httpx.ConnectTimeout("timed out")

    c = client_with(slow_handler)
    result = check_site(make_site(), client=c)
    assert result["ok"] is False
    assert "timeout" in result["error"].lower()
    assert result["status_code"] is None


def test_connect_error():
    def bad_handler(req):
        raise httpx.ConnectError("connection refused")

    c = client_with(bad_handler)
    result = check_site(make_site(), client=c)
    assert result["ok"] is False
    assert "connection refused" in result["error"].lower()


def test_ssl_expiry_non_https_returns_none():
    assert ssl_expiry_days("http://example.com") is None
    assert ssl_expiry_days("not a url") is None
