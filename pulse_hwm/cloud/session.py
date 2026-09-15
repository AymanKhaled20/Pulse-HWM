from __future__ import annotations

from dataclasses import dataclass

from pulse_hwm.cloud import token_store
from pulse_hwm.cloud.rest import AuthResult, CloudClient, Tokens

# SessionManager: owns the CURRENT session for one app run.
#
#   * access token      → RAM only (dies with the process)
#   * refresh token     → Windows Credential Manager (survives restarts)
#   * NAT on refresh    → Supabase rotates the refresh token on every
#     refresh; we immediately re-park the new one so a crash mid-refresh
#     can't leave a dead token behind.


@dataclass
class Session:
    user_id: str = ""
    email: str = ""
    provider: str = ""  # "password" | "google" | "github"

    def is_signed_in(self) -> bool:
        return bool(self.user_id)


class SessionManager:
    def __init__(self, client: CloudClient, store=token_store):
        self._client = client
        self._store = store  # injectable: tests park a fake
        self.tokens: Tokens | None = None
        self.session_info = Session()
        self.last_error = ""

    # —— state —————————————————————————————————————————————————————————
    def is_signed_in(self) -> bool:
        return self.tokens is not None and self.tokens.is_valid()

    # —— email + password ——————————————————————————————————————————————
    def sign_up(
        self,
        email: str,
        password: str,
        redirect_to: str = "",
        challenge: str = "",
    ) -> AuthResult:
        """redirect_to/challenge come from OauthCoordinator.start_email_flow()
        when confirm-email is ON (PKCE-bound verification link)."""
        result = self._client.sign_up(
            email,
            password,
            redirect_to=redirect_to,
            code_challenge=challenge,
        )
        if result.ok:
            self._adopt(result, provider="password")
        return result

    def recover(self, email: str, challenge: str = "") -> AuthResult:
        return self._client.recover(email, code_challenge=challenge)

    def sign_in(self, email: str, password: str) -> AuthResult:
        result = self._client.sign_in_with_password(email, password)
        if result.ok:
            self._adopt(result, provider="password")
        return result

    # —— OAuth (google / github) ———————————————————————————————————————
    def adopt_pkce_result(self, code: str, flow_id: str, provider: str) -> AuthResult:
        """Signature arrives via the custom scheme; finish the exchange."""
        verifier, _redirect = self._store.take_verifier(flow_id)
        if not verifier:
            return AuthResult(error="login link expired — start sign-in again")
        result = self._client.exchange_pkce_code(code, verifier)
        if result.ok:
            self.session_info.provider = provider
            self._adopt(result, provider=provider, skip_provider=True)
        return result

    # —— lifecycle —————————————————————————————————————————————————————
    def try_resume(self, force: bool = False) -> bool:
        """Silent restore on app start: does Credential Manager still
        hold a live refresh token? Returns True and sets the session.
        force=True re-runs the refresh even while signed in — the sync
        engine uses this when REST starts answering 401 because the
        access token expired since sign-in."""
        if self.is_signed_in() and not force:
            return True
        parked = self._store.load_refresh_token()
        if not parked:
            return False
        result = self._client.refresh(parked)
        if not result.ok:
            self.last_error = result.error
            if result.network_error:
                # the server never answered — keep the parked token and
                # retry on the next tick/launch; only a real rejection
                # (invalid grant) means the session is dead
                return False
            # stale token: forget it; the user just signs in again
            # (also drop the in-RAM session: leaving tokens set would
            # keep the UI painting "SIGNED IN" against a dead session)
            self._store.clear_refresh_token()
            self.tokens = None
            self.session_info = Session()
            return False
        self._adopt(result, provider="password")  # provider unknown — fine
        return True

    def sign_out(self) -> None:
        if self.tokens is not None:
            self._client.logout(self.tokens.access_token)  # best effort
        self._store.clear_refresh_token()
        self.tokens = None
        self.session_info = Session()

    # —— access token for REST calls ———————————————————————————————————
    def bearer(self) -> str:
        return self.tokens.access_token if self.tokens else ""

    # —— internals —————————————————————————————————————————————————————
    def _adopt(self, result: AuthResult, provider: str, skip_provider: bool = False):
        tokens = result.tokens
        if tokens is None:
            return result
        # Supabase rotates the refresh token on EVERY refresh — park the
        # new one instantly or a crash could orphan the session
        self._store.save_refresh_token(tokens.refresh_token)
        self.tokens = tokens
        user = result.user or {}
        identities = user.get("identities") or []
        if not skip_provider:
            provider = identities[0].get("provider") if identities else provider
        self.session_info = Session(
            user_id=str(user.get("id", "")),
            email=str(user.get("email", "")),
            provider=provider,
        )
        return result
