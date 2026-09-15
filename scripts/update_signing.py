"""Ed25519 release signing + verification for Pulse-HWM updates.

Why this exists (the trust story): the Worker stores release metadata, but
holds NO signing key. The signing key lives only in GitHub Actions
(UPDATE_SIGNING_KEY), so a compromised cloud backend cannot forge an
update. The desktop app verifies the manifest signature with an embedded
public key (see pulse_hwm/cloud/updates/trust.py: TRUSTED_UPDATE_KEYS).

Subcommands (pure stdlib + PyNaCl, no Qt, no network):
  gen     — print a fresh keypair; private = hex, public = hex
  sign    — read manifest JSON, print base64url Ed25519 signature
  verify  — verify a signature against a manifest + hex public key

CI usage (release workflow):
  python scripts/update_signing.py sign \
    --manifest demo/manifest.json --key-env UPDATE_SIGNING_KEY
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys


def _nacl():
    try:
        from nacl.signing import SigningKey, VerifyKey  # type: ignore
    except ImportError as exc:  # pragma: no cover - build-time dependency
        raise SystemExit(
            "PyNaCl is required: pip install pynacl (see requirements-dev.txt)"
        ) from exc
    return SigningKey, VerifyKey


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + pad)


def cmd_gen(_args: argparse.Namespace) -> int:
    SigningKey, _ = _nacl()
    signing = SigningKey.generate()
    private_hex = signing.encode().hex()
    public_hex = signing.verify_key.encode().hex()
    print(json.dumps({"private_hex": private_hex, "public_hex": public_hex}, indent=2))
    print(
        "// put private_hex as the GitHub secret UPDATE_SIGNING_KEY; "
        "embed public_hex in pulse_hwm/cloud/updates/trust.py",
        file=sys.stderr,
    )
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    SigningKey, _ = _nacl()
    key = os.environ.get(args.key_env, "")
    if not key:
        raise SystemExit(f"missing signing key: {args.key_env}")
    manifest_bytes = open(args.manifest, "rb").read()
    # exact bytes the app will receive and verify (never re-serialize)
    signing = SigningKey(bytes.fromhex(key))
    signature = signing.sign(manifest_bytes).signature
    print(_b64url_encode(signature))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    _, VerifyKey = _nacl()
    public_hex = args.public_key or os.environ.get("UPDATE_PUBLIC_KEY", "")
    if not public_hex:
        raise SystemExit("missing public key (--public-key or UPDATE_PUBLIC_KEY)")
    manifest_bytes = open(args.manifest, "rb").read()
    signature = _b64url_decode(open(args.signature, "r").read().strip())
    verify_key = VerifyKey(bytes.fromhex(public_hex))
    try:
        verify_key.verify(manifest_bytes, signature)
        return 0
    except Exception:
        raise SystemExit("signature INVALID")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen", help="print a fresh Ed25519 keypair (hex)")

    sign = sub.add_parser("sign", help="sign the manifest file")
    sign.add_argument("--manifest", required=True, help="path to manifest.json")
    sign.add_argument(
        "--key-env",
        default="UPDATE_SIGNING_KEY",
        help="env var holding the hex private key",
    )

    ver = sub.add_parser("verify", help="verify a signed manifest (tooling / CI check)")
    ver.add_argument("--manifest", required=True)
    ver.add_argument(
        "--signature", required=True, help="path to the base64url signature"
    )
    ver.add_argument(
        "--public-key", default="", help="hex public key (else UPDATE_PUBLIC_KEY)"
    )

    args = parser.parse_args()
    if args.cmd == "gen":
        return cmd_gen(args)
    if args.cmd == "sign":
        return cmd_sign(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
