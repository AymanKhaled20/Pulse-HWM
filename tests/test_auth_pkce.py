from __future__ import annotations

import base64
import hashlib

import pytest

from pulse_hwm.auth import pkce

# RFC 7636 Appendix B worked example (verbatim — see the scan_secrets.py
# allow-marker policy below): nothing here is a secret.
RFC_VERIFIER = (  # pulse-scan:allow
    "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # pulse-scan:allow
)
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # pulse-scan:allow


def test_pkce_rfc7636_vector():
    assert pkce.code_challenge(RFC_VERIFIER) == RFC_CHALLENGE


def test_verifier_length_bounds():
    assert len(pkce.new_code_verifier()) == 64
    with pytest.raises(ValueError):
        pkce.new_code_verifier(10)
    with pytest.raises(ValueError):
        pkce.new_code_verifier(500)


def test_verifiers_do_not_repeat():
    a, b = pkce.new_code_verifier(), pkce.new_code_verifier()
    assert a != b


def test_challenge_is_urlsafe_base64_without_padding():
    challenge = pkce.code_challenge(pkce.new_code_verifier())
    assert not challenge.endswith("=")
    assert "+" not in challenge and "/" not in challenge
    # hand-recompute to prove the formula
    v = (
        base64.urlsafe_b64encode(hashlib.sha256(RFC_VERIFIER.encode("ascii")).digest())
        .rstrip(b"=")
        .decode()
    )  # noqa: E203 — formatter disagrees; harmless
    assert v == RFC_CHALLENGE
