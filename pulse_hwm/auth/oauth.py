from __future__ import annotations

from dataclasses import dataclass

from pulse_hwm.auth import pkce
from pulse_hwm.auth import token_store as token_store_default
from pulse_hwm.auth.rest import SupabaseClient

# Custom-scheme OAuth: the app registers pulsehwm:// in the registry
# (installer), the browser redirects there, and Windows hands the URL to
# the running instance. One pending flow at a time is enforced — a
# desktop app has exactly one human, so parallel flows are a bug farm.

REDIRECT_URI = "pulsehwm://auth-callback"
CALLBACK_HOST = "pulsehwm"  # scheme
CALLBACK_PATH_PREFIX = "auth-callback"


@dataclass
class PendingFlow:
    flow_id: str
    provider: str  # "google" | "github" | "signup" | "recover"
    redirect_uri: str = REDIRECT_URI
    url: str = ""
    # needed at START time (signup/recover bodies carry the challenge);
    # the verifier itself only leaves the secure store at exchange time
    challenge: str = ""


class OauthCoordinator:
    """Starts provider sign-ins and remembers the parked PKCE verifier.

    Verifier parking lives in token_store (Credential Manager) so the
    flow survives an app restart between click and callback.
    """

    def __init__(self, client: SupabaseClient, store=token_store_default):
        self._client = client
        self._store = store
        self.pending: PendingFlow | None = None

    def start(self, provider: str) -> str:
        """Return the authorize URL to open in the system browser."""
        flow_id = pkce.new_flow_id()
        verifier = pkce.new_code_verifier()
        challenge = pkce.code_challenge(verifier)
        url = self._client.oauth_authorize_url(provider, REDIRECT_URI, challenge)
        parked = self._store.park_verifier(flow_id, verifier, REDIRECT_URI)
        if not parked:
            raise RuntimeError("secure token store unavailable — cannot sign in")
        self.pending = PendingFlow(
            flow_id=flow_id, provider=provider, url=url, challenge=challenge
        )
        return url

    def start_email_flow(self, kind: str) -> PendingFlow:
        """Parking for verify / reset emails (no browser launch here).
        The flow's challenge goes into the signup/recover POST body; the
        verifier stays parked until the link's callback arrives."""
        flow_id = pkce.new_flow_id()
        verifier = pkce.new_code_verifier()
        challenge = pkce.code_challenge(verifier)
        parked = self._store.park_verifier(flow_id, verifier, REDIRECT_URI)
        if not parked:
            raise RuntimeError("secure token store unavailable — cannot continue")
        flow = PendingFlow(flow_id=flow_id, provider=kind, challenge=challenge)
        self.pending = flow
        return flow

    def reject_pending(self) -> None:
        if self.pending is not None:
            self._store.clear_pending_flow(self.pending.flow_id)
            self.pending = None


@dataclass
class CallbackResult:
    """What a pulsehwm://auth-callback URL told us."""

    ok: bool = False
    code: str = ""
    error: str = ""


def parse_callback_url(url: str) -> CallbackResult:
    """Windows delivers the whole redirect as 'pulsehwm://auth-callback?…'.

    urllib treats the CUSTOM SCHEME weirdly (the 'host' is 'auth-callback'),
    so split query params manually instead of urlsplit games.
    """
    raw = (url or "").strip()
    if not raw.lower().startswith("pulsehwm://"):
        return CallbackResult(error="not a pulsehwm callback")
    query_start = raw.find("?")
    if query_start < 0:
        return CallbackResult(error="callback missing parameters")
    from urllib.parse import parse_qs

    params = parse_qs(raw[query_start + 1 :])
    error_desc = (params.get("error_description") or [""])[0] or (
        params.get("error") or [""]  # type: ignore[arg-type]
    )[0]
    if error_desc:
        return CallbackResult(error=error_desc[:200])
    code = (params.get("code") or [""])[0]
    if not code:
        return CallbackResult(error="callback missing auth code")
    return CallbackResult(ok=True, code=code)
