"""Update policy — PURE decision logic. No Qt, no network, no disk.

Kept free of side effects so the entire "may I install this update?"
decision tree is unit-testable (repo convention: pure logic + bridges).

Division of labour:
  * the SERVER enforces registered+active accounts (Bearer token +
    UPDATE_ACTIVITY_DAYS) — this client never even receives release data
    when it is signed out;
  * the CLIENT enforces *integrity*: only strictly-newer versions, only
    allowlisted https hosts, and (before this module runs) a fresh
    Ed25519 signature — so a compromised worker still cannot drive
    installs older than what we already know about.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Where update binaries may come from — the worker base host (which streams
# the private R2 bucket via /dl/) plus the GitHub release host family for
# the fallback asset. A compromised server can redirect us at worst inside
# these, and every file is hash + signature verified afterwards.
GITHUB_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "raw.githubusercontent.com"}
)

MAX_INSTALLER_BYTES = 400 * 1024 * 1024  # generous; installers are ~63 MB


@dataclass
class UpdateDecision:
    """What the caller should do with a (signature-verified) response."""

    state: str = "none"  # "available" | "forced" | "skipped"
    reason: str = ""
    payload: dict = None  # type: ignore[assignment]


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.match(str(text or "").strip().lstrip("vV"))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def is_rollback(candidate: str, highest_seen: str) -> bool:
    """True when the candidate is NOT strictly newer than everything we
    have ever seen — the anti-replay guard: even a faithfully signed OLD
    release must not downgrade a newer install."""
    a, b = parse_version(candidate), parse_version(highest_seen)
    if a is None or b is None:
        return False
    return a <= b


def is_below_min_supported(candidate: str, min_supported: str) -> bool:
    a, b = parse_version(candidate), parse_version(min_supported)
    if not min_supported or a is None or b is None:
        return False
    return a < b


def host_of(url: str) -> str:
    try:
        host = (urlsplit(url).netloc.lower()).split("@", 1)[-1]
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        return host
    except Exception:
        return ""


def host_allowed(url: str, extra_hosts: frozenset[str] = frozenset()) -> bool:
    """https-only + host allowlist (exact host match; the worker base host
    is passed in by the caller — it varies with self-hosted .env config)."""
    try:
        schema = urlsplit(url).scheme.lower()
    except Exception:
        return False
    if schema != "https":
        return False
    host = host_of(url)
    return bool(host) and (host in GITHUB_HOSTS or bool(host in extra_hosts))


def parse_manifest(manifest_json: str) -> dict:
    """Parse + validate the signed manifest (the exact bytes CI signed)."""
    try:
        payload = json.loads(manifest_json)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    version = str(payload.get("version", ""))
    if parse_version(version) is None:
        return {}
    if str(payload.get("asset_name", "")) != f"PulseHWM-Setup-{version}.exe":
        return {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("sha256", ""))):
        return {}
    return payload


def resolve_download_urls(release: dict, worker_base: str) -> list[str]:
    """Candidate URLs in priority order: worker /dl (R2 front-desk) first,
    GitHub release asset as fallback. Worker-relative paths get the base."""
    urls: list[str] = []
    download_url = str(release.get("download_url", "") or "")
    if download_url:
        if download_url.startswith("/"):
            urls.append(f"{worker_base.rstrip('/')}{download_url}")
        else:
            urls.append(download_url)
    fallback = str(release.get("fallback_url", "") or "")
    if fallback:
        urls.append(fallback)
    return urls


def size_within_budget(length: int | None) -> bool:
    if length is None:
        return True  # unknown streaming length is fine (capped while reading)
    return 0 <= length <= MAX_INSTALLER_BYTES


def chunk_budget_exceeded(total_read: int) -> bool:
    return total_read > MAX_INSTALLER_BYTES


# —— full decision —————————————————————————————————————————————————————


def evaluate(
    latest_release: dict,
    current_version: str,
    highest_seen: str,
    worker_base: str,
    extra_hosts: frozenset[str] = frozenset(),
    dismissed_version: str = "",
) -> UpdateDecision:
    """Full integrity/eligibility decision on a signature-VERIFIED payload.

    Order matters (fail fast, least expensive check first):
      parse version → newer?  → anti-rollback → host allowlist → mandatory?
    Signature verification happens in trust.py BEFORE this is called —
    a badly-signed payload never reaches here, so even though we re-check
    shape, this module only sees trusted data.

    `dismissed_version` is the version the user clicked LATER on (optional
    nudge-suppression, not a security control — the banner still shows).
    """
    version = str(latest_release.get("latest") or latest_release.get("version") or "")
    if parse_version(version) is None:
        return UpdateDecision("skipped", "invalid release payload", latest_release)
    if not is_newer(version, current_version):
        return UpdateDecision(
            "skipped", "not newer than the running version", latest_release
        )
    if is_rollback(version, highest_seen):
        return UpdateDecision("skipped", "rollback attempt", latest_release)
    urls = resolve_download_urls(latest_release, worker_base)
    if not any(host_allowed(u, extra_hosts) for u in urls):
        return UpdateDecision("skipped", "download host not allowed", latest_release)
    forced = bool(latest_release.get("mandatory")) or is_below_min_supported(
        str(current_version), str(latest_release.get("min_supported") or "")
    )
    if forced:
        return UpdateDecision("forced", "", latest_release)
    if dismissed_version == version:
        return UpdateDecision("skipped", "dismissed by the user", latest_release)
    return UpdateDecision("available", "", latest_release)
