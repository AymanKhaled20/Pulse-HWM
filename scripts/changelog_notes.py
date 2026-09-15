"""Extract one version's section from CHANGELOG.md (CI release step).

The changelog is the single source of release notes: the GitHub release
body AND the in-app update banner both render this text, so they can
never drift. Keeps-a-Changelog convention: headings look like
`## [1.2.0] - 2026-09-20`. Prints the section to --out (default stdout).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^## \[(?P<version>[^\]]+)\]")


def extract(changelog: str, version: str) -> str:
    """Return the body of `## [<version>]` up to the next heading."""
    lines = changelog.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            inside = match.group("version") == version
            if inside:
                continue  # heading itself is shown by GitHub's title
            if out:
                break  # hit the next section; we're done
        elif inside:
            out.append(line)
    return "\n".join(out).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", default="CHANGELOG.md")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    path = Path(args.changelog)
    if not path.exists():
        sys.stderr.write(f"no {args.changelog}; using a one-line body\n")
        notes = text_fallback(args.version)
    else:
        notes = extract(
            path.read_text(encoding="utf-8"), args.version
        ) or text_fallback(args.version)
    if args.out:
        Path(args.out).write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)
    return 0


def text_fallback(version: str) -> str:
    return f"Pulse-HWM v{version}\n"


if __name__ == "__main__":
    raise SystemExit(main())
