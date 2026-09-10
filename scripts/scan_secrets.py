#!/usr/bin/env python3
"""Pulse-HWM pre-commit secret scanner.

Scans files staged for commit (or explicit paths with --paths) for secret-like
patterns: provider API keys, private keys, webhooks, and high-entropy strings.
Exit codes: 0 clean, 1 leak found, 2 internal error.

Usage:
    py scripts/scan_secrets.py --staged          # scan staged files (default)
    py scripts/scan_secrets.py --paths a.py b.py # scan specific files
    py scripts/scan_secrets.py --all             # scan all tracked files
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# file name patterns never scanned (binaries, vendored, generated)
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".zip", ".7z", ".gz", ".tar", ".exe", ".dll", ".so", ".dylib",
    ".pdf", ".db", ".sqlite3", ".pyc", ".pyo", ".wav", ".mp3", ".ogg",
}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "build", "dist", ".pytest_cache", "node_modules"}
SKIP_FILE_NAMES = {"package-lock.json", "poetry.lock", "Pipfile.lock"}

# never flag placeholder values commonly seen in example/template files
PLACEHOLDER_SUBSTRINGS = {
    "your_key_here", "your_key", "your-key", "yourtoken", "your_token",
    "placeholder", "example_key", "example.com", "xxxxxxxx", "paste_your",
    "<insert", "insert_here", "changeme", "dummy", "sample_key",
}

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Anthropic-style key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("GitHub token (classic)", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack bot/user token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Slack incoming webhook", re.compile(r"\bhooks\.slack\.com/services/T[A-Za-z0-9]{8,}\b")),
    ("Discord webhook URL", re.compile(r"https://discord(app)?\.com/api/webhooks/\d{5,}/[A-Za-z0-9_-]{30,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b")),
    ("Stripe secret key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}\b")),
    ("npm/PyPI token", re.compile(r"\b(npm_[A-Za-z0-9]{36}|pypi-[A-Za-z0-9_]{20,})\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("generic apiKey= assignment", re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|auth[_-]?token)\b\s*[:=]\s*[\"'][A-Za-z0-9+/_-]{24,}[\"']")),
    ("bearer token literal", re.compile(r"(?i)\bbearer\s+[\"'][A-Za-z0-9._~+/-]{30,}[\"']")),
]

HIGH_ENTROPY_RE = re.compile(r"\b[A-Za-z0-9+/_=-]{40,}\b")

# lines that look like code but never carry secrets
SAFE_LINE_RE = re.compile(r"^\s*(#|//|<!--|\"\"\"|\'\'\')")


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def is_placeholder(text: str) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in PLACEHOLDER_SUBSTRINGS)


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True, cwd=REPO_ROOT,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True, cwd=REPO_ROOT,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def iter_candidate_lines(raw: bytes):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    for i, line in enumerate(text.splitlines(), start=1):
        yield i, line


def scan_file(rel_path: str) -> list[tuple[int, str, str]]:
    """Return [(line_no, kind, redacted_snippet)] hits for one file."""
    path = REPO_ROOT / rel_path
    if not path.is_file():
        return []
    if path.suffix.lower() in SKIP_SUFFIXES:
        return []
    if path.name in SKIP_FILE_NAMES or any(part in SKIP_DIRS for part in path.parts):
        return []

    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:          # binary → skip
        return []

    hits: list[tuple[int, str, str]] = []
    is_env_file = path.name == ".env" or path.name.startswith(".env.")

    for line_no, line in iter_candidate_lines(raw):
        stripped = line.strip()
        safe_to_skip = SAFE_LINE_RE.match(stripped) is not None
        content = stripped if stripped else line

        if is_env_file:
            # .env / .env.* must never be committed at all (except nobody should
            # stage .env.example either — it lives in repo as a tracked template;
            # allow .env.example explicitly)
            if path.name != ".env.example":
                hits.append((line_no, "committed .env file", "<blocked>"))
                break

        for kind, pattern in SECRET_PATTERNS:
            m = pattern.search(content)
            if not m:
                continue
            token = m.group(0)
            if is_placeholder(token):
                continue
            show = token[:8] + "…REDACTED…" + token[-4:] if len(token) > 16 else "…REDACTED…"
            hits.append((line_no, kind, show))

        if not safe_to_skip:
            for token in HIGH_ENTROPY_RE.findall(content):
                if is_placeholder(token):
                    continue
                if shannon_entropy(token) >= 4.8 and len(set(token)) >= 12:
                    show = token[:8] + "…REDACTED…" + token[-4:]
                    hits.append((line_no, "high-entropy string", show))
                    break  # one hit per line is enough
    return hits


def run(args: argparse.Namespace) -> int:
    if args.all:
        files = tracked_files()
    elif args.paths:
        files = args.paths
    else:
        files = staged_files()

    if not files:
        print("[pulse-scan] nothing to scan")
        return 0

    print(f"[pulse-scan] scanning {len(files)} file(s)...")
    total_hits = 0
    for rel in sorted(files):
        hits = scan_file(rel)
        for line_no, kind, snippet in hits:
            print(f"[pulse-scan] LEAK  {rel}:{line_no}  {kind}  ->  {snippet}")
            total_hits += 1

    if total_hits:
        print(f"\n[pulse-scan] COMMIT BLOCKED — {total_hits} possible secret(s) found.")
        print("[pulse-scan] Move it to .env (gitignored) and reference it via os.environ.")
        print("[pulse-scan] If this is a false positive, adjust scan_secrets.py allowlists.")
        return 1
    print("[pulse-scan] clean ✔ no secrets found")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Pulse-HWM secret scanner")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="scan files staged for commit (default)")
    group.add_argument("--all", action="store_true", help="scan every tracked file")
    group.add_argument("--paths", nargs="+", metavar="FILE", help="scan specific files")
    parser.set_defaults(staged=True)
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:  # internal error → hard fail, never silently pass
        print(f"[pulse-scan] ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
