from __future__ import annotations

import re
import unicodedata

# Registration-time validation. Pure functions, no Qt, no network —
# cheap to unit-test and safe to call from any thread.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

_MIN_PASSWORD_LEN = 8

_PASSWORD_HINTS = {
    "lower": "a lowercase letter",
    "upper": "an uppercase letter",
    "digit": "a digit",
}


def validate_email(email: str) -> str:
    """'' when acceptable, otherwise a short reason."""
    email = (email or "").strip()
    if not email:
        return "enter an email address"
    if not _EMAIL_RE.match(email):
        return "that does not look like a valid email"
    return ""


def validate_password(password: str) -> str:
    """'' when acceptable, otherwise a HUMAN, actionable message.

    Supabase enforces its own policy server-side; this mirrors a sane
    minimum so users aren't surprised after the network round trip.
    """
    password = password or ""
    if len(password) < _MIN_PASSWORD_LEN:
        return f"password must be at least {_MIN_PASSWORD_LEN} characters"
    if not password.strip():
        return "password cannot be only spaces"
    return ""


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def clean_email(raw: str) -> str:
    """Trim + lowercase an email before it ever crosses the network.

    Also strips zero-width/format characters (U+200B–200D, U+2060, U+FEFF)
    — NFKC alone does NOT remove those, and they're a classic look-alike
    trick for cookie/phishing lookalikes."""
    text = _normalize((raw or "").strip().lower())
    return text.translate({ord(c): None for c in "\u200b\u200c\u200d\u2060\ufeff"})
