"""SessionStart hook (matcher "compact"): re-inject HANDOFF.md after compaction.

HANDOFF.md is the running "read me after compaction" state file. SessionStart
stdout is added to Claude's context, so this makes that happen automatically.
Costs roughly the file's size in tokens on each compaction.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    handoff = project_dir / "HANDOFF.md"
    if not handoff.is_file():
        return 0

    text = handoff.read_text(encoding="utf-8", errors="replace")
    header = "Context restored after compaction. HANDOFF.md (trust the code if it disagrees):\n\n"
    # Write raw UTF-8 bytes: the Windows console codepage (cp1252) cannot
    # encode the box-drawing/arrow characters HANDOFF.md uses.
    sys.stdout.buffer.write((header + text).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
