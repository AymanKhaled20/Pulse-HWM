"""Update trust anchors — the app-level verification layer.

Two independent anchors; BOTH must pass before an installer may run:

1. Ed25519 manifest signature (PyNaCl). The private key lives ONLY in
   GitHub Actions; the matching public keys are embedded below, so a
   compromised cloud worker CANNOT forge an update. Fail-closed: with no
   trusted key provisioned, updates are DISABLED by design until the
   keypair is generated once (scripts/update_signing.py gen) and the
   public half is pasted here.
2. Authenticode (Windows). WinVerifyTrust confirms the installer has a
   signature chaining to a trusted root — defense-in-depth against a
   tampered download even if the manifest hash somehow agreed.
"""

from __future__ import annotations

import base64
import ctypes
import sys

from pulse_hwm.cloud.updates.policy import parse_manifest

# —— anchor 1: Ed25519 —————————————————————————————————————————————————
#
# Provisioning: `python scripts/update_signing.py gen` → paste public_hex
# strings here (private key goes to GitHub Actions ONLY). Key rotation =
# append the new key first, retire the old one after every client has
# adopted a version that embeds the addition.

TRUSTED_UPDATE_KEYS: frozenset[str] = frozenset(
    {
        # example: "32-hex-words-here-64-chars-total-..."
    }
)

SUPPORTED_ALGS = frozenset({"ed25519"})

# Flip to True ONCE SignPath signs every production release. Until then
# the sha256 + Ed25519 pair is the binding identity, and a bare WinVerify
# check would reject our own unsigned CI builds.
AUTHENTICODE_REQUIRED = False


def signature_alg_ok(alg: str) -> bool:
    return str(alg or "").strip().lower() in SUPPORTED_ALGS


def manifest_signature_ok(
    manifest_json: str,
    sig_b64url: str,
    trusted_keys: frozenset[str] | None = None,
) -> bool:
    """Verify a base64url Ed25519 signature over the EXACT manifest string.

    Returns False (never raises) when: no keys provisioned (fail-closed),
    empty inputs, malformed signature, or no trusted key matches. The
    manifest contents are validated with parse_manifest first so a
    well-signed but /inconsistent/ manifest can never pass either.
    """
    keys = TRUSTED_UPDATE_KEYS if trusted_keys is None else trusted_keys
    if not keys or not manifest_json or not sig_b64url:
        return False
    if not parse_manifest(manifest_json):
        return False
    try:
        from nacl.exceptions import BadSignatureError  # type: ignore
        from nacl.signing import VerifyKey  # type: ignore
    except ImportError:
        return False  # no crypto available → fail-closed

    try:
        raw_sig = urlsafe_b64decode(sig_b64url)
    except Exception:
        return False
    manifest_bytes = manifest_json.encode("utf-8")
    for hex_pub in keys:
        try:
            VerifyKey(bytes.fromhex(hex_pub)).verify(manifest_bytes, raw_sig)
            return True  # one trusted key is enough
        except BadSignatureError:
            continue
        except Exception:
            continue
    return False


def urlsafe_b64decode(data: str) -> bytes:
    """base64url with/without padding."""
    return base64.urlsafe_b64decode(
        data.strip() + "=" * ((4 - len(data.strip()) % 4) % 4)
    )


# —— anchor 2: Authenticode (WinVerifyTrust) ——————————————————————————
#
# LONG WinVerifyTrust(HWND, GUID*, WINTRUST_DATA*); 0 == the Authenticode
# chain validates to a trusted root. Subject pinning (a specific signer)
# is deliberately NOT enforced yet — the sha256 + Ed25519 checks bound the
# file to the exact CI build; Authenticode here is defense-in-depth.

WINTRUST_ACTION_GENERIC_VERIFY_V2_GUID = (
    0x00AAC56B,
    0xCD44,
    0x11D0,
    (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
)

# enums from wintrust.h
_WTD_UI_NONE = 2
_WTD_REVOKE_NONE = 0
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_VERIFY = 1
_WTD_STATEACTION_CLOSE = 2


class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbStruct", ctypes.c_ulong),
        ("pcwszFilePath", ctypes.c_wchar_p),
        ("hFile", ctypes.c_void_p),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class WINTRUST_DATA(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [
            ("pFile", ctypes.c_void_p),
        ]

    _fields_ = [
        ("cbStruct", ctypes.c_ulong),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", ctypes.c_ulong),
        ("fdwRevocationChecks", ctypes.c_ulong),
        ("dwUnionChoice", ctypes.c_ulong),
        ("pFile", ctypes.c_void_p),
        ("dwStateAction", ctypes.c_ulong),
        ("hWVTStateData", ctypes.c_void_p),
        ("pSignatureSettings", ctypes.c_void_p),
        ("dwUIContext", ctypes.c_ulong),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _generic_verify_v2_guid() -> _GUID:
    d1, d2, d3, tail = WINTRUST_ACTION_GENERIC_VERIFY_V2_GUID
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = d1, d2, d3
    for i, byte in enumerate(tail):
        g.Data4[i] = byte
    return g


def authenticode_available() -> bool:
    return sys.platform == "win32"


def authenticode_verified(path: str) -> bool:
    """True iff lib validates the file against a trusted root (WinVerifyTrust).
    False (never raises) on non-Windows, missing file, or any failure."""
    if not authenticode_available():
        return False
    if not path or not sys.path:
        return False
    try:
        _ = ctypes.windll.wintrust  # library exists → proceed
    except AttributeError:
        return False
    try:
        info = WINTRUST_FILE_INFO()
        info.cbStruct = ctypes.sizeof(WINTRUST_FILE_INFO)
        info.pcwszFilePath = str(path)
        data = WINTRUST_DATA()
        data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
        data.dwUIChoice = _WTD_UI_NONE
        data.fdwRevocationChecks = _WTD_REVOKE_NONE
        data.dwUnionChoice = _WTD_CHOICE_FILE
        data.pFile = ctypes.cast(ctypes.byref(info), ctypes.c_void_p)
        data.dwStateAction = _WTD_STATEACTION_VERIFY
        action = _generic_verify_v2_guid()
        res = ctypes.windll.wintrust.WinVerifyTrust(
            ctypes.c_void_p(0), ctypes.byref(action), ctypes.byref(data)
        )
        ok = res == 0
        # release the verification state it allocated (StateAction hygiene)
        data.dwStateAction = _WTD_STATEACTION_CLOSE
        ctypes.windll.wintrust.WinVerifyTrust(
            ctypes.c_void_p(0), ctypes.byref(action), ctypes.byref(data)
        )
        return ok
    except Exception:
        return False
