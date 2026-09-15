from __future__ import annotations

import base64
import json

from pulse_hwm.cloud.updates import trust

# Full Ed25519 round trip with REAL keys (nacl.signing) — the exact trust
# gate CI creates (sign) and the app enforces (verify). Fail-closed rules:
# no provisioned keys ⇒ False; tampered manifest ⇒ False; wrong key ⇒ False.


def _fresh_keys():
    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    return sk, sk.verify_key.encode().hex()


def _manifest(version: str = "1.2.0") -> str:
    return json.dumps(
        {
            "version": version,
            "asset_name": f"PulseHWM-Setup-{version}.exe",
            "sha256": "a" * 64,
        },
        separators=(",", ":"),
    )


def _sign(sk, manifest: str) -> str:
    sig = sk.sign(manifest.encode("utf-8")).signature  # exact bytes = contract
    return base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")


def test_signature_verification_round_trip():
    sk, pub = _fresh_keys()
    manifest = _manifest()
    sig = _sign(sk, manifest)
    assert trust.manifest_signature_ok(manifest, sig, {sk.verify_key.encode().hex()})


def test_fail_closed_with_no_trusted_keys():
    sk, _ = _fresh_keys()
    manifest = _manifest()
    assert trust.manifest_signature_ok(manifest, _sign(sk, manifest), set()) is False


def test_rejects_wrong_key_and_tampered_manifest():
    sk, pub = _fresh_keys()
    other, _ = _fresh_keys()
    manifest = _manifest()
    sig = _sign(sk, manifest)
    # signed by a key we do not trust
    assert trust.manifest_signature_ok(manifest, sig, {other}) is False
    # tampered AFTER signing: even the real key must reject
    assert (
        trust.manifest_signature_ok(
            manifest.replace("1.2.0", "1.3.0"), sig, {sk.verify_key.encode().hex()}
        )
        is False
    )


def test_rejects_malformed_inputs():
    sk, pub_hex = _fresh_keys()
    manifest = _manifest()
    assert trust.manifest_signature_ok(manifest, "!!!notbase64!!!", {pub_hex}) is False
    assert trust.manifest_signature_ok("", _sign(sk, manifest), {pub_hex}) is False


def test_unsupported_alg_rejected():
    assert trust.signature_alg_ok("ed25519")
    assert not trust.signature_alg_ok("md5")


def test_authenticode_gate_is_windows_aware():
    import sys

    if sys.platform != "win32":
        assert not trust.authenticode_available()
