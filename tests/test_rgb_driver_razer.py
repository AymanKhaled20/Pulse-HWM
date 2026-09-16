"""Razer Chroma REST driver tests — offline via httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from pulse_hwm.rgb.drivers.razer_chroma import RazerDriver
from pulse_hwm.rgb.model import RgbColor

SESSION_URI = "http://localhost:54235/razer/chromasdk/1234"


def router(request: httpx.Request) -> httpx.Response:
    path = request.url.path.rstrip("/")
    method = request.method
    if path.endswith("/razer/chromasdk") and method == "GET":
        return httpx.Response(200, json={"version": "4.0"})
    if path.endswith("/razer/chromasdk") and method == "POST":
        return httpx.Response(200, json={"sessionid": 1234, "uri": SESSION_URI})
    if path.endswith("/heartbeat") and method == "PUT":
        return httpx.Response(200, json={})
    if path.endswith("/keyboard") and method == "POST":
        return httpx.Response(200, json={"id": 42})
    if path.endswith("/effects") and method == "PUT":
        return httpx.Response(200, json={})
    if path.endswith("/1234") and method == "DELETE":
        return httpx.Response(200, json={})
    return httpx.Response(404, json={})


@pytest.fixture()
def driver():
    return RazerDriver(
        timeout=1.0,
    )


def attach(driver: RazerDriver) -> None:
    client = httpx.Client(transport=httpx.MockTransport(router), timeout=1.0)
    driver._client = client
    driver.open()


class TestProbe:
    def test_server_present(self):
        transport = httpx.MockTransport(router)
        driver = RazerDriver(timeout=1.0)
        driver._client = httpx.Client(transport=transport, timeout=1.0)
        assert driver.probe().available is True

    def test_server_absent(self):
        def broken_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        driver = RazerDriver(timeout=0.5)
        driver._client = httpx.Client(
            transport=httpx.MockTransport(broken_handler), timeout=0.5
        )
        probe = driver.probe()
        assert probe.available is False
        assert "Synapse" in probe.needs_install


class TestSession:
    def test_open_returns_uri(self):
        driver = RazerDriver(timeout=1.0)
        attach(driver)
        assert driver._session_uri == SESSION_URI

    def test_close_deletes_session(self):
        driver = RazerDriver(timeout=1.0)
        attach(driver)
        driver.close()
        assert driver._session_uri is None


class TestSetFrame:
    def test_static_effect_created_and_activated(self):
        driver = RazerDriver(timeout=1.0)
        attach(driver)
        ok = driver.set_frame("razer:keyboard", [RgbColor(255, 0, 0)] * 4)
        assert ok is True

    def test_identical_frame_heartbeats_within_interval(self):
        driver = RazerDriver(timeout=1.0)
        attach(driver)
        red = [RgbColor(255, 0, 0)] * 4
        assert driver.set_frame("razer:keyboard", red) is True
        clock = {"now": 0.0}
        driver._clock = lambda: clock["now"]
        assert driver.set_frame("razer:keyboard", red) is True  # inside hold
        clock["now"] = 2.0
        assert driver.set_frame("razer:keyboard", red) is True  # heartbeat

    def test_not_open_rejects(self):
        driver = RazerDriver(timeout=1.0)
        assert driver.set_frame("x", [RgbColor()] * 2) is False
