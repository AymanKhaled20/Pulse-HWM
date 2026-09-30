"""PostToolUse hook: black + ruff --fix the Python file Claude just edited.

Mirrors the pre-commit pipeline (black first, then ruff) so files are already
clean when a commit runs. Silent on success so it costs no context tokens;
only unfixable ruff findings are reported back (exit 2 -> shown to Claude).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0  # malformed payload: never block an edit over it

    file_path = event.get("tool_input", {}).get("file_path", "")
    path = Path(file_path)
    if path.suffix != ".py" or not path.is_file():
        return 0

    # Formatting problems are fixed silently; black exits non-zero only on
    # a syntax error, which the ruff pass below reports anyway.
    subprocess.run(
        [sys.executable, "-m", "black", "--quiet", str(path)],
        capture_output=True,
    )
    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--fix", "--quiet", str(path)],
        capture_output=True,
        text=True,
    )
    if lint.returncode != 0:
        print(
            f"ruff found issues it could not auto-fix in {path.name}:", file=sys.stderr
        )
        print(lint.stdout.strip() or lint.stderr.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
