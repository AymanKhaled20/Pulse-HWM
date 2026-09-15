from __future__ import annotations

import base64
import hashlib
import secrets
import string

# RFC 7636 PKCE. A public desktop client has no client secret, so the
# code verifier IS the proof: challenge = BASE64URL(SHA256(verifier)).
# Deterministic and unit-testable against the RFC's worked example.

_VERIFIER_ALPHABET = string.ascii_letters + string.digits + "-._~"


def new_code_verifier(length: int = 64) -> str:
    """Unpacked verifier (43–128 chars allowed; 64 is a comfy middle)."""
    if not 43 <= length <= 128:
        raise ValueError("PKCE verifier must be 43–128 chars")
    return "".join(secrets.choice(_VERIFIER_ALPHABET) for _ in range(length))


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_flow_id() -> str:
    """Short random id tying a browser session to a parked verifier."""
    return secrets.token_urlsafe(9)  # 12 chars, urlsafe by construction
