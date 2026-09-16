"""Razer Chroma driver — Chroma SDK REST (localhost:54235), no DLL.

Backend: Synapse's REST server. Needles:
  * POST base URI with app info → per-app session uri (15s idle timeout!)
  * PUT {session}/heartbeat every second keeps Synapse holding the app;
    the engine runs at rgb_engine_fps so set_frame drives it during active
    frames — we add a >=1 s heartbeat when the frame is unchanged
  * static effect: POST {session}/keyboard {"effect":"CHROMA_STATIC",
    "param":{"type":"two","color":<0xRRGGBB int>}} → id, then
    PUT {session}/effects {"id": <effect id>} activates it
Injected httpx client keeps all of this off-network in tests (MockTransport).
"""

from __future__ import annotations

import time

import httpx

from pulse_hwm.rgb.drivers.base import ProbeResult, RgbDriver
from pulse_hwm.rgb.model import RgbColor

BASE = "http://localhost:54235/razer/chromasdk"
HEARTBEAT_INTERVAL_S = 1.0
DEFAULT_TIMEOUT_S = 4.0


def _app_info() -> dict:
    return {
        "title": "Pulse-HWM",
        "description": "Hardware monitor RGB control",
        "author": {"name": "Pulse-HWM", "contact": "@pulsehwm"},
        "device_supported": ["keyboard"],
        "category": "application",
    }


def _percent(value: int) -> int:  # not used today, kept for parity
    return value


def _representative(colors: list[RgbColor]) -> RgbColor | None:
    if not colors:
        return None
    first = colors[0]
    if all((c.r, c.g, c.b) == (first.r, first.g, first.b) for c in colors):
        return first
    count = len(colors)
    return RgbColor(
        sum(c.r for c in colors) // count,
        sum(c.g for c in colors) // count,
        sum(c.b for c in colors) // count,
    )


class RazerDriver(RgbDriver):
    driver_id = "razer_chroma"
    name = "Razer Chroma"
    version = "1"
    requires_admin = False
    requires_app = "Razer Synapse"

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_S):
        self._client = httpx.Client(timeout=timeout)
        self._session_uri: str | None = None
        self._last_color: RgbColor | None = None
        self._last_heartbeat_monotonic: float | None = None
        self._clock = time.monotonic
        self.last_error: str = ""

    # ── probe / open / close ────────────────────────────────────────────
    def probe(self) -> ProbeResult:
        try:
            response = self._client.get(BASE, timeout=2.0)
        except Exception:
            return ProbeResult(
                False,
                "Razer Chroma SDK server not running — install Razer Synapse",
                needs_install="Razer Synapse",
            )
        if response.status_code != 200:
            return ProbeResult(False, f"Chroma server answered {response.status_code}")
        return ProbeResult(True)

    def open(self) -> None:
        response = self._client.post(BASE, json=_app_info())
        if response.status_code != 200:
            raise RuntimeError(f"session create failed: {response.status_code}")
        payload = response.json()
        uri = payload.get("uri")
        if not uri:
            raise RuntimeError(f"no session uri in response: {payload}")
        self._session_uri = str(uri)
        self._last_color = None

    def close(self) -> None:
        if self._session_uri is not None:
            try:
                self._client.delete(self._session_uri)
            except Exception:
                pass
        self._session_uri = None

    # ── devices / frames ────────────────────────────────────────────────
    def devices(self) -> list:
        from pulse_hwm.rgb.model import RgbDevice

        return [
            RgbDevice(
                device_id="razer:keyboard",
                name="Razer Keyboard",
                driver_id=self.driver_id,
                leds=1,
                modes=frozenset({"static"}),
            )
        ]

    def set_frame(self, device_id: str, colors: list[RgbColor]) -> bool:
        del device_id
        if self._session_uri is None:
            self.last_error = "not open"
            return False
        representative = _representative(colors)
        if representative is None:
            return False
        now = self._clock()
        if representative == self._last_color:
            if self._last_heartbeat_monotonic is not None and (
                now - self._last_heartbeat_monotonic < HEARTBEAT_INTERVAL_S
            ):
                return True  # within Synapse's hold window, nothing to send
            try:
                heartbeat = self._client.put(f"{self._session_uri}/heartbeat")
            except Exception as exc:
                self.last_error = f"heartbeat failed: {exc}"
                return False
            self._last_heartbeat_monotonic = now
            return heartbeat.status_code == 200
        color_int = (
            (representative.r << 16) | (representative.g << 8) | representative.b
        )
        try:
            create = self._client.post(
                f"{self._session_uri}/keyboard",
                json={
                    "effect": "CHROMA_STATIC",
                    "param": {"type": "two", "color": color_int},
                },
            )
            if create.status_code != 200 and create.status_code != 201:
                self.last_error = f"effect create failed: {create.status_code}"
                return False
            effect_id = create.json().get("id")
            activate = self._client.put(
                f"{self._session_uri}/effects", json={"id": effect_id}
            )
        except Exception as exc:
            self.last_error = f"effect request failed: {exc}"
            return False
        if activate.status_code not in (200, 201):
            self.last_error = f"effect activate failed: {activate.status_code}"
            return False
        self._last_heartbeat_monotonic = now
        self._last_color = representative
        return True

    def set_brightness(self, device_id: str, pct: int) -> bool:
        del device_id, pct
        return False
