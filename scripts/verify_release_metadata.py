"""Check a staged release EXACTLY the way the desktop app will (CI step).

Why this exists: v1.3.0 shipped with a signature over the manifest FILE
(which had a trailing CRLF) while the worker was sent the manifest STRING
(no newline). Every client then refused the update as "signature invalid"
and nothing in CI noticed. This script runs the app's own trust + policy
code against metadata.json before anything is published, so a release
that clients would reject fails the build instead.

Checks:
  * the Ed25519 signature verifies over metadata["manifest"] with the
    public keys embedded in the app (trust.TRUSTED_UPDATE_KEYS)
  * the signed manifest names the tag's version and asset
  * the signed sha256 equals the real installer file's hash

Usage:
  python scripts/verify_release_metadata.py --metadata dist-assets/metadata.json
      --installer dist-assets/PulseHWM-Setup-1.4.0.exe --version 1.4.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# run as a plain script from the repo root: make `pulse_hwm` importable
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pulse_hwm.cloud.updates import policy, trust  # noqa: E402


def sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def find_problems(
    metadata: dict,
    installer_sha256: str,
    version: str,
    trusted_keys: frozenset[str] | None = None,
) -> list[str]:
    """Every reason a client would refuse this release (empty = good).

    `trusted_keys` is only for tests; CI uses the keys baked into the app.
    """
    problems: list[str] = []
    manifest = str(metadata.get("manifest", "") or "")
    signature = str(metadata.get("manifest_sig", "") or "")

    signed = policy.parse_manifest(manifest)
    if not signed:
        problems.append("manifest is missing or not a valid signed-manifest shape")
        return problems

    if not trust.manifest_signature_ok(manifest, signature, trusted_keys):
        problems.append(
            "signature does not verify over the manifest string the worker "
            "will serve (signed different bytes? wrong key?)"
        )
    if signed.get("version") != version:
        problems.append(f"manifest version {signed.get('version')} != tag {version}")
    if str(metadata.get("version", "")) != version:
        problems.append(f"metadata version {metadata.get('version')} != tag {version}")
    if signed.get("sha256") != installer_sha256.lower():
        problems.append("manifest sha256 does not match the installer file")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, help="path to metadata.json")
    parser.add_argument("--installer", required=True, help="path to the setup .exe")
    parser.add_argument("--version", required=True, help="X.Y.Z from the tag")
    args = parser.parse_args()

    # utf-8-sig: tolerate a BOM in case a future PowerShell writes one
    metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8-sig"))
    problems = find_problems(
        metadata, sha256_of_file(Path(args.installer)), args.version
    )
    if problems:
        for problem in problems:
            print(f"release check FAILED: {problem}", file=sys.stderr)
        return 1
    print(f"release check ok: v{args.version} verifies like a client would")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
