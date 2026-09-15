from __future__ import annotations

import time

# Windows Credential Manager via keyring. Key facts we rely on:
#   * backed by DPAPI → the blob is bound to THIS Windows user account
#   * tokens never land in files the .gitignore policies would worry about
#   * a missing backend (CI, bare container) degrades to a refusal,
#     never an exception that would crash the app

_SERVICE = "PulseHWM"
_REFRESH_ACCOUNT = "supabase-refresh"
# PKCE verifiers are single-use and short-lived; keyed by a flow id
_VERIFIER_PREFIX = "pkce-"
# pointer to the latest unfinished flow (so a cold launch can complete it)
_PENDING_ACCOUNT = "pending-flow"
_VERIFIER_TTL_S = 15 * 60  # a dropped email link is dead after 15 minutes


def _keyring():
    import keyring

    return keyring


def available() -> bool:
    """True when a usable secure backend exists (Credential Manager on
    Windows). When False, sign-in stays disabled rather than writing
    tokens to disk."""
    try:
        return _keyring().get_keyring() is not None
    except Exception:
        return False


def _delete(account: str) -> None:
    try:
        _keyring().delete_password(_SERVICE, account)
    except Exception:
        pass  # already gone / no backend


# —— refresh token (long-lived; the crown jewel) ———————————————————————


def save_refresh_token(token: str) -> bool:
    if not token:
        _delete(_REFRESH_ACCOUNT)
        return True
    try:
        _keyring().set_password(_SERVICE, _REFRESH_ACCOUNT, token)
        return True
    except Exception:
        return False


def load_refresh_token() -> str:
    try:
        return _keyring().get_password(_SERVICE, _REFRESH_ACCOUNT) or ""
    except Exception:
        return ""


def clear_refresh_token() -> None:
    _delete(_REFRESH_ACCOUNT)


# —— PKCE verifier parking ————————————————————————————————————————————
# Survives an app restart between "send the verify/reset email" and
# "the user finally clicks it". Payload: parked_at|verifier|redirect_dst


def _verifier_account(flow_id: str) -> str:
    # account names must be printable/no spaces for Credential Manager
    safe = "".join(c for c in flow_id if c.isalnum() or c in "-_")[:64]
    return f"{_VERIFIER_PREFIX}{safe}"


def park_verifier(flow_id: str, code_verifier: str, redirect_to: str) -> bool:
    if not flow_id or not code_verifier:
        return False
    try:
        _keyring().set_password(
            _SERVICE,
            _verifier_account(flow_id),
            f"{time.time():.0f}|{code_verifier}|{redirect_to}",
        )
        return True
    except Exception:
        return False


def take_verifier(flow_id: str) -> tuple[str, str]:
    """Fetch + erase the parked verifier. ("", "") if missing/expired."""
    if not flow_id:
        return "", ""
    try:
        raw = _keyring().get_password(_SERVICE, _verifier_account(flow_id)) or ""
    except Exception:
        return "", ""
    _delete(_verifier_account(flow_id))  # single-use whatever happens next
    try:
        parked_at, verifier, redirect = raw.split("|", 2)
        if time.time() - float(parked_at) > _VERIFIER_TTL_S:
            return "", ""
    except ValueError:
        return "", ""
    return verifier, redirect


def clear_pending_flow(flow_id: str) -> None:
    _delete(_verifier_account(flow_id))


# —— pending flow pointer —————————————————————————————————————————————
# The callback URL carries ONLY the auth code — we need to know which
# parked verifier matches. In-process, OauthCoordinator.pending remembers
# it; the pointer below lets a COLD launch (user clicked the email link
# while the app was closed) reconstruct the flow too. Value: flow_id|provider


def save_pending_flow(flow_id: str, provider: str) -> bool:
    try:
        _keyring().set_password(_SERVICE, _PENDING_ACCOUNT, f"{flow_id}|{provider}")
        return True
    except Exception:
        return False


def load_pending_flow() -> tuple[str, str]:
    """Stored pointer (flow_id, provider); ("","") when none/invalid."""
    try:
        raw = _keyring().get_password(_SERVICE, _PENDING_ACCOUNT) or ""
    except Exception:
        return "", ""
    parts = raw.split("|")
    if len(parts) != 2 or not parts[0]:
        return "", ""
    return parts[0], parts[1]


def clear_pending_flow_id() -> None:
    _delete(_PENDING_ACCOUNT)
