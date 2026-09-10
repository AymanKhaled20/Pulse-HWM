from __future__ import annotations

import io
import struct
import threading
import wave
from dataclasses import dataclass

import httpx

from pulse_hwm import config


def synth_beep() -> bytes:
    """Short 2-tone square-wave alert, 16-bit mono 8kHz."""
    rate = 8000
    tones = [
        (880.0, 0.16),
        (1174.7, 0.22),
    ]
    frames = bytearray()
    for freq, dur in tones:
        n_samples = int(rate * dur)
        half = max(1, int(rate / freq / 2))
        for i in range(n_samples):
            frames.extend(struct.pack("<h", 12000 if (i // half) % 2 == 0 else -12000))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))
    return buf.getvalue()


_WIN_SND = None


def _win_sound():
    global _WIN_SND
    if _WIN_SND is None:
        try:
            import winsound as snd
            _WIN_SND = snd
        except Exception:
            _WIN_SND = False
    return _WIN_SND


def play_alert_sound() -> None:
    """Non-blocking in-memory WAV playback. No-op off Windows."""
    snd = _win_sound()
    if not snd:
        return
    try:
        snd.PlaySound(synth_beep(), snd.SND_MEMORY | snd.SND_ASYNC)
    except Exception:
        pass


def send_webhooks(url: str, payload: dict, timeout: float = 6.0) -> tuple[bool, str]:
    """POST an alert payload. Returns (ok, error). URL content never logged."""
    if not url:
        return False, "no webhook configured"
    try:
        response = httpx.post(url, json=payload, timeout=timeout)
        return (200 <= response.status_code < 300), f"status {response.status_code}"
    except Exception as exc:
        return False, _safe_error(str(exc))


def _safe_error(message: str) -> str:
    """Strip anything that could leak the webhook URL from an error string."""
    keep = message.split("\n")[0][:120]
    for marker in ("https://", "http://"):
        if marker in keep:
            keep = keep.split(marker)[0] + "<url redacted>"
            break
    return keep


def send_discord_alert(message: str) -> None:
    env = config.env()
    if env.discord_webhook_url:
        threading.Thread(
            target=send_webhooks,
            args=(env.discord_webhook_url, {"content": f"🟡 {message}"}),
            daemon=True,
        ).start()


def send_slack_alert(message: str) -> None:
    env = config.env()
    if env.slack_webhook_url:
        threading.Thread(
            target=send_webhooks,
            args=(env.slack_webhook_url, {"text": f"🟡 {message}"}),
            daemon=True,
        ).start()


def send_toast(title: str, message: str) -> None:
    try:
        from winotify import Notification, audio
        toast = Notification(app_id="Pulse-HWM", title=title, msg=message, duration="short")
        try:
            audio.Default(toast)
        except Exception:
            pass
        toast.show()
    except Exception:
        pass


@dataclass
class AlertChannels:
    sound: bool = True
    desktop: bool = True
    webhooks: bool = True


class AlertManager:
    """Central alert dispatcher. Wires website transitions + history log."""

    def __init__(self, db, channels: AlertChannels | None = None):
        self._db = db
        self._channels = channels or AlertChannels()
        self._tray = None

    def set_channels(self, channels: AlertChannels) -> None:
        self._channels = channels

    def attach_tray(self, tray) -> None:
        self._tray = tray

    def notify(self, level: str, title: str, message: str, play_sound: bool = True) -> None:
        if self._channels.desktop and self._tray is not None:
            try:
                from PySide6.QtWidgets import QSystemTrayIcon
                severity = QSystemTrayIcon.MessageIcon.Critical if level == "error" else QSystemTrayIcon.MessageIcon.Information
                self._tray.showMessage(title, message, severity, 5000)
            except Exception:
                pass
        if self._channels.sound and play_sound:
            threading.Thread(target=play_alert_sound, daemon=True).start()
        if self._channels.webhooks:
            threading.Thread(
                target=_fan_out_webhooks, args=(f"{title} — {message}", level), daemon=True
            ).start()

    def handle_site_transition(self, result: dict) -> None:
        """Slot for WebsiteMonitor.site_state_changed."""
        if result["transition"] == "down":
            reason = result.get("error") or f"status {result.get('status_code')}"
            message = f"{result['name']} is DOWN — {reason}"
            self.notify("error", "SITE DOWN", message, play_sound=True)
        else:
            message = f"{result['name']} recovered ({result['latency_ms']} ms)"
            self.notify("info", "SITE RECOVERED", message, play_sound=False)
        self._db.insert_event(
            _now(), "ERROR" if result["transition"] == "down" else "INFO",
            f"site_{result['transition']}", message,
        )


def _fan_out_webhooks(message: str, level: str) -> None:
    env = config.env()
    if env.discord_webhook_url:
        send_webhooks(env.discord_webhook_url, {"content": f"[{level}] {message}"})
    if env.slack_webhook_url:
        send_webhooks(env.slack_webhook_url, {"text": f"[{level}] {message}"})


def _now() -> float:
    import time
    return time.time()
