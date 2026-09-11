from __future__ import annotations

import io
import struct
import threading
import wave
from dataclasses import dataclass

import httpx

from pulse_hwm import config

# Error sound: a DESCENDING chain of square-wave tones (E5 → C5 → G#4 → E#4/D#4).
# A fall reads as "something is wrong" to the ear; the old ascending two-tone
# (880 → 1174.7 Hz) sounded like a happy "ta-da!" instead of an alert.
ALERT_TONES: tuple[tuple[float, float], ...] = (
    (659.3, 0.10),  # E5
    (523.3, 0.10),  # C5
    (415.3, 0.10),  # G#4
    (311.1, 0.26),  # D#4 — long low tail = the "womp"
)


def synth_beep() -> bytes:
    """Short 4-tone square-wave ERROR alert, 16-bit mono 8kHz."""
    rate = 8000
    tones = ALERT_TONES
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
# PlaySound(SND_MEMORY) blocked (kept alive via _LAST_WAV_BYTES) runs on a
# daemon thread so notify() never stalls; buffer must outlive the call.
_LAST_WAV_BYTES: bytes | None = None
last_sound_error: str | None = None


def _win_sound():
    # last_sound_error is a module-level global so the UI can show why sound
    # is unavailable; assigning without `global` would create a throwaway local.
    global _WIN_SND, last_sound_error
    if _WIN_SND is None:
        try:
            import winsound as snd

            _WIN_SND = snd
        except Exception as exc:
            _WIN_SND = False
            last_sound_error = f"import: {exc}"
    return _WIN_SND


def _play_blocking(snd, data: bytes) -> None:
    global last_sound_error
    try:
        snd.PlaySound(data, snd.SND_MEMORY)
    except Exception as exc:
        last_sound_error = f"play: {exc}"


def play_alert_sound() -> bool:
    """Background in-memory WAV playback. Returns True when dispatched."""
    global _LAST_WAV_BYTES, last_sound_error
    snd = _win_sound()
    if not snd:
        return False
    try:
        _LAST_WAV_BYTES = synth_beep()
        threading.Thread(
            target=_play_blocking, args=(snd, _LAST_WAV_BYTES), daemon=True
        ).start()
        last_sound_error = None
        return True
    except Exception as exc:
        last_sound_error = f"play: {exc}"
        return False


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

        toast = Notification(
            app_id="Pulse-HWM", title=title, msg=message, duration="short"
        )
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

    def notify(
        self, level: str, title: str, message: str, play_sound: bool = True
    ) -> dict:
        """Dispatch an alert on all enabled channels. Returns what fired."""
        sent = {"sound": False, "desktop": False, "webhooks": 0}
        if self._channels.desktop and self._tray is not None:
            try:
                from PySide6.QtWidgets import QSystemTrayIcon

                severity = (
                    QSystemTrayIcon.MessageIcon.Critical
                    if level == "error"
                    else QSystemTrayIcon.MessageIcon.Information
                )
                self._tray.setToolTip(title)
                self._tray.showMessage(title, message, severity, 5000)
                sent["desktop"] = True
            except Exception:
                pass
        if self._channels.sound and play_sound:
            threading.Thread(
                target=(lambda: sent.__setitem__("sound", play_alert_sound())),
                daemon=True,
            ).start()
            sent["sound_requested"] = True
        if self._channels.webhooks:
            threading.Thread(
                target=lambda: sent.__setitem__(
                    "webhooks", _fan_out_webhooks(f"{title} — {message}", level)
                ),
                daemon=True,
            ).start()
        return sent

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
            _now(),
            "ERROR" if result["transition"] == "down" else "INFO",
            f"site_{result['transition']}",
            message,
        )


def _fan_out_webhooks(message: str, level: str) -> int:
    """POST to all configured webhooks. Returns how many were attempted."""
    env = config.env()
    attempts = 0
    if env.discord_webhook_url:
        send_webhooks(env.discord_webhook_url, {"content": f"[{level}] {message}"})
        attempts += 1
    if env.slack_webhook_url:
        send_webhooks(env.slack_webhook_url, {"text": f"[{level}] {message}"})
        attempts += 1
    return attempts


def _now() -> float:
    import time

    return time.time()
