"""Post release metadata to the Pulse-HWM cloud worker (CI step).

Calls POST /updates/publish with the shared RELEASE_KEY header. The worker
validates everything (semver, sha256, manifest<->fields agreement,
anti-downgrade) and upserts into the D1 `releases` table.

Env:
  RELEASE_KEY        — shared publish secret (same as worker secret)
  PULSEHWM_CLOUD_URL — worker base URL (default matches auth/config.py)

Usage:
  python scripts/publish_release.py --metadata metadata.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

# CI's console is cp1252 on windows-latest; a non-ASCII glyph in a success
# print crashed the process (exit 1) AFTER the publish had already landed,
# letting the workflow report failure for a release that fully succeeded.
# Force a UTF-8-tolerant stdout so print never kills a finished job again.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_CLOUD_URL = "https://pulsehwm-cloud.pulsehwm27.workers.dev"
PUBLISH_PATH = "/updates/publish"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata", required=True, help="path to release metadata JSON"
    )
    args = parser.parse_args()

    key = os.environ.get("RELEASE_KEY", "")
    if not key:
        raise SystemExit("RELEASE_KEY is not set")

    metadata = json.load(open(args.metadata, encoding="utf-8"))
    base = os.environ.get("PULSEHWM_CLOUD_URL", DEFAULT_CLOUD_URL).rstrip("/")

    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            f"{base}{PUBLISH_PATH}",
            headers={"x-pulse-release-key": key},
            json=metadata,
        )
    if 200 <= resp.status_code < 300:
        print(f"published: {metadata.get('version')} -> {base}")
        return 0
    # never echo the key; the body is a worker error message
    print(
        f"publish failed: http {resp.status_code}: {resp.text[:200]}", file=sys.stderr
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
