from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def scanner():
    spec = importlib.util.spec_from_file_location(
        "scan_secrets", REPO / "scripts" / "scan_secrets.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_secrets"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scratch_dir(tmp_path):
    """scan_file resolves paths relative to REPO_ROOT — point it at tmp."""
    return tmp_path


def run_scan(scanner, content: str, tmp_path: Path):
    probe = tmp_path / "probe_file.txt"
    probe.write_text(content, encoding="utf-8")
    original = scanner.REPO_ROOT
    scanner.REPO_ROOT = tmp_path
    try:
        return scanner.scan_file("probe_file.txt")
    finally:
        scanner.REPO_ROOT = original


def test_clean_file_passes(scanner, tmp_path):
    hits = run_scan(scanner, "def main():\n    return 42\n", tmp_path)
    assert hits == []


def test_openai_key_detected(scanner, tmp_path):
    content = (
        "key = '" + "sk-proj-" + "abcdef0123456789abcdef0" + "'\n"
    )  # pulse-scan:allow gitleaks:allow
    hits = run_scan(scanner, content, tmp_path)
    assert any("OpenAI" in kind for _, kind, _ in hits)


def test_google_key_detected(scanner, tmp_path):
    content = (
        "storage.googleapis.com?key="
        + "AIza"
        + "SyA0123456789"
        + "abcdef0123456789"
        + "abcdef0"
        + "\n"
    )  # pulse-scan:allow gitleaks:allow
    hits = run_scan(scanner, content, tmp_path)
    assert any("Google" in kind for _, kind, _ in hits)


def test_aws_key_detected(scanner, tmp_path):
    content = (
        "aws_access = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'\n"
    )  # pulse-scan:allow gitleaks:allow (AWS docs sample)
    hits = run_scan(scanner, content, tmp_path)
    assert any("AWS" in kind for _, kind, _ in hits)


def test_discord_webhook_detected(scanner, tmp_path):
    # token split across two short literals: the scanner reads SOURCE lines, and
    # a >40-char alnum token on one line looks like a real high-entropy secret.
    content = (
        "url = 'https://discord.com/api/webhooks/"
        + "112233445566778890"
        + "/"
        + "abCdEfGhIjKlMnOpQrStUvWxYz1234"
        + "567890abcd"
        + "'\n"
    )  # pulse-scan:allow gitleaks:allow
    hits = run_scan(scanner, content, tmp_path)
    assert any("Discord" in kind for _, kind, _ in hits)


def test_private_key_block_detected(scanner, tmp_path):
    content = (
        "-----BEGIN " + "RSA PRIVATE KEY" + "-----\n" + "MIIEowIB" + "\n"
    )  # pulse-scan:allow gitleaks:allow
    hits = run_scan(scanner, content, tmp_path)
    assert any("private key" in kind.lower() for _, kind, _ in hits)


def test_placeholders_allowed(scanner, tmp_path):
    plain = run_scan(scanner, "DISCORD_WEBHOOK_URL=\nAPI_KEY=your_key_here\n", tmp_path)
    assert plain == []


def test_high_entropy_detected(scanner, tmp_path):
    content = (
        "token = '" + "Zx8Kp2Qw9Er5Ty6Ui3Op4As7Df8Gh1" + "Jk5Lz0XcVb2Nm4Qr7Wf1" + "'\n"
    )  # pulse-scan:allow gitleaks:allow
    hits = run_scan(scanner, content, tmp_path)
    assert any(kind == "high-entropy string" for _, kind, _ in hits)


def test_allow_marker_bypasses(scanner, tmp_path):
    fake = "sk-" + "test-fake-key-abcdef012345"  # pulse-scan:allow gitleaks:allow
    content = "secret = '" + fake + "'  " + "# pulse-scan:allow" + "\n"
    hits = run_scan(scanner, content, tmp_path)
    assert hits == []


def test_unmarked_fixture_still_blocked(scanner, tmp_path):
    content = (
        "key = '" + "sk-proj-" + "abcdef0123456789abcdef0" + "'\n"
    )  # pulse-scan:allow gitleaks:allow (content is unmarked, source is marked)
    hits = run_scan(scanner, content, tmp_path)
    assert any("OpenAI" in kind for _, kind, _ in hits)


def test_env_file_never_commit(scanner, tmp_path):
    path = tmp_path / ".env"
    path.write_text("SOMETHING=1\n", encoding="utf-8")
    original = scanner.REPO_ROOT
    scanner.REPO_ROOT = tmp_path
    try:
        hits = scanner.scan_file(".env")
    finally:
        scanner.REPO_ROOT = original
    assert any(kind == "committed .env file" for _, kind, _ in hits)


def test_env_example_allowed(scanner, tmp_path):
    original = scanner.REPO_ROOT
    scanner.REPO_ROOT = tmp_path
    try:
        (tmp_path / ".env.example").write_text(
            "DISCORD_WEBHOOK_URL=\n", encoding="utf-8"
        )
        hits = scanner.scan_file(".env.example")
    finally:
        scanner.REPO_ROOT = original
    assert not any(kind == "committed .env file" for _, kind, _ in hits)


def test_binary_file_skipped(scanner, tmp_path):
    path = tmp_path / "probe_file.bin"
    path.write_bytes(b"\x00\x01\x02\x03" + b"A" * 100)
    original = scanner.REPO_ROOT
    scanner.REPO_ROOT = tmp_path
    try:
        hits = scanner.scan_file("probe_file.bin")
    finally:
        scanner.REPO_ROOT = original
    assert hits == []
