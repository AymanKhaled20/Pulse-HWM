from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALLER_BYTES = b"pretend installer"
INSTALLER_SHA = hashlib.sha256(INSTALLER_BYTES).hexdigest()
VERSION = "1.4.0"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location(
        "verify_release_metadata", REPO / "scripts" / "verify_release_metadata.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_release_metadata"] = module
    spec.loader.exec_module(module)
    return module


def _manifest() -> str:
    # same shape (and spacing) as the string release.yml builds
    return json.dumps(
        {
            "version": VERSION,
            "asset_name": f"PulseHWM-Setup-{VERSION}.exe",
            "sha256": INSTALLER_SHA,
            "published_at": "2026-09-30T00:00:00+00:00",
            "mandatory": False,
            "min_supported": "1.1.6",
        }
    )


def _sign(signed_bytes: bytes):
    """Sign like scripts/update_signing.py; returns (signature, public hex)."""
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    signature = key.sign(signed_bytes).signature
    sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return sig_b64, key.verify_key.encode().hex()


def test_good_release_passes(guard):
    manifest = _manifest()
    sig, pub = _sign(manifest.encode("utf-8"))
    meta = {"version": VERSION, "manifest": manifest, "manifest_sig": sig}
    assert guard.find_problems(meta, INSTALLER_SHA, VERSION, frozenset({pub})) == []


def test_signature_over_file_with_trailing_newline_is_caught(guard):
    # the v1.3.0 bug: CI signed the file (string + CRLF) but published the
    # bare string, so every client rejected the signature
    manifest = _manifest()
    sig, pub = _sign((manifest + "\r\n").encode("utf-8"))
    meta = {"version": VERSION, "manifest": manifest, "manifest_sig": sig}
    problems = guard.find_problems(meta, INSTALLER_SHA, VERSION, frozenset({pub}))
    assert any("signature" in p for p in problems)


def test_signing_key_not_trusted_by_the_app_is_caught(guard):
    # e.g. the UPDATE_SIGNING_KEY secret was rotated but trust.py was not
    manifest = _manifest()
    sig, _signer_pub = _sign(manifest.encode("utf-8"))
    _other_sig, other_pub = _sign(b"unrelated")
    meta = {"version": VERSION, "manifest": manifest, "manifest_sig": sig}
    problems = guard.find_problems(meta, INSTALLER_SHA, VERSION, frozenset({other_pub}))
    assert any("signature" in p for p in problems)


def test_wrong_installer_hash_is_caught(guard):
    manifest = _manifest()
    sig, pub = _sign(manifest.encode("utf-8"))
    meta = {"version": VERSION, "manifest": manifest, "manifest_sig": sig}
    problems = guard.find_problems(meta, "b" * 64, VERSION, frozenset({pub}))
    assert any("sha256" in p for p in problems)


def test_tag_mismatch_is_caught(guard):
    manifest = _manifest()
    sig, pub = _sign(manifest.encode("utf-8"))
    meta = {"version": VERSION, "manifest": manifest, "manifest_sig": sig}
    problems = guard.find_problems(meta, INSTALLER_SHA, "1.4.1", frozenset({pub}))
    assert any("tag" in p for p in problems)


def test_missing_manifest_is_caught(guard):
    problems = guard.find_problems({"version": VERSION}, INSTALLER_SHA, VERSION)
    assert problems
