from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from pulse_hwm.cloud.updates import installer, policy
from pulse_hwm.cloud.updates.installer import (
    UpdateError,
    asset_name_of,
    download_installer,
    expect_sha256,
    install_command,
    pick_download_url,
    verify_installer,
)

WORKER = "https://pulsehwm-cloud.pulsehwm27.workers.dev"
ASSET = "PulseHWM-Setup-1.2.0.exe"


def _make_manifest(body: bytes) -> str:
    sha = hashlib.sha256(body).hexdigest()
    return '{"version": "1.2.0", "asset_name": "%s", "sha256": "%s"}' % (ASSET, sha)


def _release(body: bytes, **over) -> dict:
    release = {
        "version": "1.2.0",
        "asset_name": ASSET,
        "sha256": hashlib.sha256(body).hexdigest(),
        "manifest": _make_manifest(body),
        "manifest_sig": "sig",
        "download_url": f"/dl/{ASSET}",
        "fallback_url": f"https://github.com/AymanKhaled20/Pulse-HWM/releases/download/v1.2.0/{ASSET}",
    }
    release.update(over)
    return release


# —— URL selection ————————————————————————————————————————————————————
def test_pick_url_prefers_worker_then_falls_back():
    assert pick_download_url(_release(b"ab"), WORKER) == f"{WORKER}/dl/{ASSET}"
    no_worker = {
        "download_url": "https://evil.tld/x.exe",
        "fallback_url": f"https://github.com/a/b/releases/download/v1.2.0/{ASSET}",
    }
    assert pick_download_url(no_worker, WORKER).startswith("https://github.com/")


def test_pick_url_refuses_untrusted_hosts():
    with pytest.raises(UpdateError):
        pick_download_url({"download_url": "https://evil.tld/pwn.exe"}, WORKER)


# —— sha binding comes from the SIGNED manifest, never gloss ——————————
def test_expect_sha_ignores_top_level_gloss():
    body = b"\x00\x01\x02"
    release = _release(body, sha256="f" * 64)  # decorated but WRONG
    assert expect_sha256(release) == hashlib.sha256(body).hexdigest()


def test_install_command_is_silent_and_relaunching():
    cmd = install_command("C:/x/PulseHWM-Setup-1.3.0.exe")
    assert str(cmd[-3]).upper() == "/SILENT"
    assert "/NORESTART" in cmd and "/LAUNCHAFTER=1" in cmd
    assert cmd[0].endswith(".exe")


def test_spawn_injection_never_gets_real_process(tmp_path):
    captured = {}

    def fake_spawn(cmd, **kw):
        captured["cmd"] = cmd
        return object()

    installer.spawn_installer(install_command(tmp_path / "setup.exe"), spawn=fake_spawn)
    assert captured["cmd"][0].endswith("setup.exe")
    assert captured["cmd"][-1] == "/LAUNCHAFTER=1"


# —— download + verify (MockTransport; fake progress) ————————————————
def _http_handler(payload: bytes, calls: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        calls["auth"] = request.headers.get("authorization", "")
        calls["url"] = str(request.url)
        return httpx.Response(
            200,
            content=payload,
            headers={"content-length": str(len(payload))},
        )

    return httpx.MockTransport(handler)


def test_download_streams_verifies_and_lands_atomically(tmp_path):
    payload = b"PULSE-INSTALLER-BINARY" * 1000
    release = _release(payload)
    transport = _http_handler(payload, calls := {})
    seen: list[tuple[int, int | None]] = []
    path = download_installer(
        release,
        WORKER,
        access_token="tok-123",
        dest_dir=tmp_path,
        on_progress=lambda done, total: seen.append((done, total)),
        transport=transport,
    )
    assert Path(path).name == ASSET
    assert path.read_bytes() == payload
    assert not Path(installer.staging_path(path)).exists()  # staging cleaned
    # the Bearer travels to the worker front desk
    assert calls["auth"] == "Bearer tok-123"
    assert calls["url"].startswith(WORKER)
    # progress reported the final full-size chunk
    assert seen[-1][0] == len(payload)


def test_download_reports_progress_even_without_total(tmp_path):
    payload = b"x" * 2048
    handler_transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=payload)  # no explicit length header
    )
    seen = []
    path = download_installer(
        _release(payload),
        WORKER,
        "t",
        tmp_path,
        on_progress=lambda d, t: seen.append((d, t)),
        transport=handler_transport,
    )
    assert path.exists() and path.read_bytes() == payload
    # the streaming bytes themselves are what matters: final block = full size
    assert seen[-1][0] == 2048
    assert seen[-1][1] in (None, 2048)  # indeterminate only when no length


def test_bad_checksum_never_lands_a_file(tmp_path):
    payload = b"malware"
    release = {
        "version": "1.2.0",
        "asset_name": ASSET,
        "sha256": "b" * 64,
        "manifest": bad_manifest_json(ASSET),
        "manifest_sig": "sig",
        "download_url": f"/dl/{ASSET}",
        "fallback_url": "",
    }
    transport = _http_handler(payload, {})
    with pytest.raises(UpdateError, match="checksum"):
        download_installer(release, WORKER, "t", tmp_path, transport=transport)
    assert not list(tmp_path.glob("*.part"))
    # and the mismatch body was NOT written as the final asset
    assert not (tmp_path / ASSET).exists()


def bad_manifest_json(asset: str) -> str:
    return '{"version": "1.2.0", "asset_name": "%s", "sha256": "%s"}' % (
        asset,
        "b" * 64,
    )


def test_download_giant_content_length_is_refused(tmp_path):
    release = _release(b"x")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"content-length": "999999999999"}, content=b""
        )
    )
    with pytest.raises(UpdateError, match="size"):
        download_installer(release, WORKER, "t", tmp_path, transport=transport)


def test_stream_overflow_without_content_length_is_capped(tmp_path):
    # the running-total cap is enforced while streaming (no content-length)
    assert installer.chunk_budget_exceeded(policy.MAX_INSTALLER_BYTES + 1)
    assert not installer.chunk_budget_exceeded(1024)


def test_download_uses_bearer_header(tmp_path):
    payload = b"z" * 10
    calls = {}
    transport = _http_handler(payload, calls)
    download_installer(_release(payload), WORKER, "tok", tmp_path, transport=transport)
    assert calls.get("auth") == "Bearer tok"
    assert calls.get("url", "").endswith(f"/dl/{ASSET}")


# —— verification gate (pre-spawn TOCTOU shrink) —————————————————————
def test_verify_installer_passes_on_matching_hash(tmp_path):
    payload = b"valid-binary-content"
    path = tmp_path / ASSET
    path.write_bytes(payload)
    verify_installer(path, hashlib.sha256(payload).hexdigest())  # no raise


def test_verify_installer_rejects_mismatch_and_deletes(tmp_path):
    path = tmp_path / ASSET
    path.write_bytes(b"tampered")
    expect = hashlib.sha256(b"original").hexdigest()
    with pytest.raises(UpdateError, match="checksum"):
        verify_installer(path, expect)
    assert not path.exists()  # destructive: deleted, never installable


def test_verify_installer_rejects_missing_file(tmp_path):
    with pytest.raises(UpdateError):
        verify_installer(tmp_path / "nope.exe", "0" * 64)


def test_asset_name_contract(tmp_path):
    release = _release(b"x")
    assert asset_name_of(release) == ASSET
    with pytest.raises(UpdateError):
        asset_name_of({"asset_name": "evil.tld.exe"})
