from __future__ import annotations

import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from pulse_hwm.db import Database


class SiteCheckResult(dict):
    @property
    def site_id(self) -> int:
        return self["site_id"]

    @property
    def ok(self) -> bool:
        return self["ok"]


def check_site(site: dict, client: httpx.Client | None = None) -> SiteCheckResult:
    """Blocking single-website check. Runs on worker threads.
    `client` injectable for tests (httpx.MockTransport)."""
    url = site["url"]
    method = (site.get("method") or "GET").upper()
    timeout_s = float(site.get("timeout_s") or 10.0)
    expected = int(site.get("expected_status") or 200)
    keyword = (site.get("keyword") or "").strip()
    started = time.perf_counter()
    status_code: int | None = None
    error = ""
    ok = False
    try:
        response = (client or httpx).request(
            method, url, timeout=timeout_s, follow_redirects=True
        )
        status_code = response.status_code
        ok = response.status_code == expected
        if ok and keyword:
            body = response.text[-200_000:]
            if keyword.lower() not in body.lower():
                ok = False
                error = f"keyword '{keyword[:20]}' not found"
    except httpx.TimeoutException:
        error = f"timeout after {timeout_s:.0f}s"
    except httpx.HTTPError as exc:
        error = _short_error(str(exc))
    except Exception as exc:
        error = _short_error(str(exc))
    latency_ms = (time.perf_counter() - started) * 1000.0
    return SiteCheckResult(
        site_id=int(site["id"]),
        name=site.get("name", ""),
        url=url,
        ts=time.time(),
        status_code=status_code,
        latency_ms=round(latency_ms, 1),
        ok=ok,
        error=error,
    )


def _short_error(message: str) -> str:
    message = message.strip().replace("\n", " ")
    return message[:160] if len(message) > 160 else message


def ssl_expiry_days(url: str, timeout_s: float = 6.0) -> int | None:
    """Days until TLS certificate expiry (https only)."""
    try:
        parts = urlsplit(url)
        if parts.scheme != "https":
            return None
        host = parts.hostname
        if not host:
            return None
        port = parts.port or 443
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        not_after = cert.get("notAfter") if cert else None
        if not not_after:
            return None
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime

        expires = parsedate_to_datetime(not_after)
        if not isinstance(expires, datetime):
            return None
        expires = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
        return int((expires - datetime.now(timezone.utc)).total_seconds() // 86400)
    except Exception:
        return None


SITES_REFRESH_ROWS = 5


class WebsiteMonitor(QObject):
    checked = Signal(dict)  # every finished check
    site_state_changed = Signal(dict)  # down↔recovery transition
    ssl_updated = Signal(int, int)  # site_id, days_left

    def __init__(
        self,
        db: Database,
        interval_s: int = 30,
        timeout_s: float = 10.0,
        ssl_warn_days: int = 14,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.db = db
        self._db = db
        self._interval_s = max(5, interval_s)
        self._timeout_s = timeout_s
        self._ssl_warn_days = ssl_warn_days
        self._states: dict[int, bool] = {}
        self._fail_streaks: dict[int, int] = {}
        self._check_counts: dict[int, int] = {}
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pulse-site")

    def config(self) -> dict:
        return {
            "interval_s": self._interval_s,
            "timeout_s": self._timeout_s,
            "ssl_warn_days": self._ssl_warn_days,
        }

    def reconfigure(
        self,
        interval_s: int | None = None,
        timeout_s: float | None = None,
        ssl_warn_days: int | None = None,
    ) -> None:
        if interval_s is not None:
            self._interval_s = max(5, int(interval_s))
        if timeout_s is not None:
            self._timeout_s = float(timeout_s)
        if ssl_warn_days is not None:
            self._ssl_warn_days = int(ssl_warn_days)
        if getattr(self, "_timer", None) is not None:
            self._timer.setInterval(self._interval_s * 1000)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.run_cycle)
        self._timer.start(self._interval_s * 1000)
        self.run_cycle()

    def stop(self) -> None:
        if getattr(self, "_timer", None):
            self._timer.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def run_cycle(self) -> None:
        sites = [dict(row) for row in self._db.get_sites(include_disabled=False)]
        pending: list = []
        for site in sites:
            pending.append(self._pool.submit(self._check_one, site))
            self._check_counts[site["id"]] = self._check_counts.get(site["id"], 0) + 1
        for future in pending:
            future.add_done_callback(lambda _f: None)

    def run_cycle_now(self) -> None:
        self.run_cycle()

    # -- internals -----------------------------------------------------------
    def _check_one(self, site: dict) -> None:
        result = check_site(site)
        site_id = result["site_id"]
        self._db.insert_check(
            site_id,
            result["ts"],
            result["status_code"],
            result["latency_ms"],
            result["ok"],
            result["error"],
        )
        previous = self._states.get(site_id)
        if result["ok"]:
            self._fail_streaks[site_id] = 0
        else:
            self._fail_streaks[site_id] = self._fail_streaks.get(site_id, 0) + 1
        self._states[site_id] = result["ok"]
        notify_down = (previous is not False) and not result["ok"]
        if notify_down:
            payload = dict(result)
            payload["transition"] = "down"
            payload["streak"] = self._fail_streaks[site_id]
            self.site_state_changed.emit(payload)
        elif previous is False and result["ok"]:
            payload = dict(result)
            payload["transition"] = "recovered"
            self.site_state_changed.emit(payload)
        self.checked.emit(result)

        count = self._check_counts[site_id]
        if count % 10 == 1 or count == 1:
            self._refresh_ssl(site)

    def _refresh_ssl(self, site: dict) -> None:
        days = ssl_expiry_days(site["url"], timeout_s=self._timeout_s)
        if days is not None:
            self.ssl_updated.emit(site["id"], days)


class WebsiteThreadBridge:
    @staticmethod
    def attach(
        thread: QThread,
        db: Database,
        interval_s: int,
        timeout_s: float,
        ssl_warn_days: int,
    ) -> WebsiteMonitor:
        monitor = WebsiteMonitor(
            db, interval_s=interval_s, timeout_s=timeout_s, ssl_warn_days=ssl_warn_days
        )
        monitor.moveToThread(thread)
        thread.started.connect(monitor.start)
        thread.finished.connect(monitor.stop)
        return monitor


def uptime_percent(db: Database, site_id: int, window_s: float) -> float | None:
    """Rolling uptime % over a window; None when no data."""
    rows = db.checks_since(site_id, time.time() - window_s)
    if not rows:
        return None
    ok_count = sum(1 for r in rows if r["ok"])
    return 100.0 * ok_count / len(rows)
