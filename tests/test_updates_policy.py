from __future__ import annotations

import pytest

from pulse_hwm.cloud.updates import policy

WORKER = "https://pulsehwm-cloud.pulsehwm27.workers.dev"
WORKER_HOST = policy.host_of(WORKER)


# ── version comparisons ───────────────────────────────────────────────
def test_parse_version_basic_and_prefix():
    assert policy.parse_version("1.2.0") == (1, 2, 0)
    assert policy.parse_version("v1.2.10") == (1, 2, 10)
    assert policy.parse_version("") is None
    assert policy.parse_version("1.2") is None
    assert policy.parse_version("banana") is None


def test_is_newer_semantics():
    assert policy.is_newer("1.1.10", "1.1.9")  # numeric, not lexical
    assert not policy.is_newer("1.1.6", "1.1.6")
    assert not policy.is_newer("1.1.5", "1.1.6")


def test_is_rollback_blocks_downgrades_and_replays():
    assert policy.is_rollback("1.1.5", "1.1.6")
    assert policy.is_rollback("1.1.6", "1.1.6")  # replay of the same version
    assert not policy.is_rollback("1.1.7", "1.1.6")


def test_below_min_supported_flags_forced_security_fix():
    assert policy.is_below_min_supported("1.1.0", "1.1.4")
    assert not policy.is_below_min_supported("1.1.6", "1.1.4")
    assert not policy.is_below_min_supported("1.1.6", "")


# ── host allowlist ────────────────────────────────────────────────────
def test_host_allowlist_requires_https():
    assert policy.host_allowed(
        "https://github.com/AymanKhaled20/x/releases/download/v1.2.0/a.exe"
    )
    assert policy.host_allowed("https://objects.githubusercontent.com/a/b")
    assert policy.host_allowed(f"{WORKER}/dl/PulseHWM-Setup-1.2.0.exe", {WORKER_HOST})
    assert not policy.host_allowed("http://evil.tld/x.exe")
    assert not policy.host_allowed("https://evil.tld/x.exe")
    # subdomain spoofing must NOT pass an exact-host allowlist
    assert not policy.host_allowed("https://evilgithub.com/x.exe")
    assert not policy.host_allowed("")


# ── manifest parsing ──────────────────────────────────────────────────
def _manifest(version="1.2.0", sha="a" * 64) -> str:
    return (
        '{"version": "%s", "asset_name": "PulseHWM-Setup-%s.exe", "sha256": "%s"}'
        % (version, version, sha)
    )


def test_parse_manifest_accepts_consistent_payload():
    parsed = policy.parse_manifest(_manifest())
    assert parsed["version"] == "1.2.0"
    assert parsed["asset_name"] == "PulseHWM-Setup-1.2.0.exe"


@pytest.mark.parametrize(
    "manufactured",
    [
        "not json",
        '{"version": "banana", "asset_name": "x", "sha256": "b" * 32}',
        '{"version": "1.2.0", "asset_name": "PulseHWM-Setup-1.2.0.exe", "sha256": "zz"}',
        '{"version": "1.2.0", "asset_name": "PulseHWM-Setup-9.9.9.exe", "sha256": "'
        + "a" * 64
        + '"}',
    ],
)
def test_parse_manifest_rejects_mismatches(manufactured):
    assert policy.parse_manifest(manufactured) == {}


# ── resolve_download_urls ─────────────────────────────────────────────
def test_resolve_urls_worker_first_then_fallback():
    urls = policy.resolve_download_urls(
        {
            "download_url": "/dl/PulseHWM-Setup-1.2.0.exe",
            "fallback_url": "https://github.com/a/releases/download/v1.2.0/PulseHWM-Setup-1.2.0.exe",
        },
        WORKER,
    )
    assert urls[0].startswith(WORKER)
    assert urls[1].startswith("https://github.com/")


# ── full decision ─────────────────────────────────────────────────────
def _payload(version="1.2.0", **over):
    p = {
        "version": version,
        "download_url": f"/dl/PulseHWM-Setup-{version}.exe",
        "mandatory": False,
        "min_supported": "",
    }
    p.update(over)
    return p


def test_evaluate_available():
    d = policy.evaluate(_payload(), "1.1.6", "1.1.6", WORKER, {WORKER_HOST})
    assert d.state == "available"


def test_evaluate_forced_when_mandatory():
    d = policy.evaluate(
        _payload(mandatory=True), "1.1.6", "1.1.6", WORKER, {WORKER_HOST}
    )
    assert d.state == "forced"


def test_evaluate_forced_below_security_floor():
    # running 1.1.3 while the cloud says 1.1.4+ is the security floor
    d = policy.evaluate(
        _payload(min_supported="1.1.4"), "1.1.3", "1.1.3", WORKER, {WORKER_HOST}
    )
    assert d.state == "forced"


def test_evaluate_rejects_rollback_even_signed():
    # 1.1.5 IS newer than the running 1.1.4, but we already saw 1.1.7
    d = policy.evaluate(_payload("1.1.5"), "1.1.4", "1.1.7", WORKER, {WORKER_HOST})
    assert d.state == "skipped" and "rollback" in d.reason


def test_evaluate_skips_when_not_newer():
    d = policy.evaluate(_payload("1.1.6"), "1.1.6", "1.1.6", WORKER, {WORKER_HOST})
    assert d.state == "skipped" and "not newer" in d.reason


def test_evaluate_honors_dismissed_version():
    d = policy.evaluate(
        _payload(), "1.1.6", "1.1.6", WORKER, {WORKER_HOST}, dismissed_version="1.2.0"
    )
    assert d.state == "skipped"


def test_evaluate_rejects_evil_host():
    d = policy.evaluate(
        {"version": "1.2.0", "download_url": "https://evil.tld/pwn.exe"},
        "1.1.6",
        "1.1.6",
        WORKER,  # the worker itself is an allowed host, but not evil.tld
        set(),
    )
    assert d.state == "skipped" and "host" in d.reason
