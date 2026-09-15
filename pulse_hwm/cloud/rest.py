from __future__ import annotations

from dataclasses import dataclass, field

import httpx

# —— dataclasses (pure data; ideal for offline testing) ————————————————


@dataclass
class Tokens:
    """A Supabase session: short-lived access + long-lived refresh."""

    access_token: str = ""
    refresh_token: str = ""
    expires_in: int = 0

    def is_valid(self) -> bool:
        return bool(self.access_token and self.refresh_token)


@dataclass
class AuthResult:
    """Uniform result for every auth call; `error` is a friendly message."""

    ok: bool = False
    tokens: Tokens | None = None
    user: dict = field(default_factory=dict)
    # needs_email_confirmation: signup succeeded but Supabase sent a
    # verification link first — no tokens exist yet
    needs_email_confirmation: bool = False
    error: str = ""
    # True only when the request never reached the server (offline/DNS
    # down): the caller must NOT treat this as a rejected session
    network_error: bool = False


def _parse_error(payload: object, fallback: str) -> str:
    """Pull a human message out of Supabase's several error shapes.

    Never echoes anything secret — these payloads are error descriptions,
    not tokens. Unknown shapes collapse to the fallback.
    """
    if isinstance(payload, dict):
        # {error, error_description} (older) / {code, msg} (newer)
        for keys in (("error", "error_description"), ("msg",), ("message",)):
            if keys[0] in payload:
                text = " ".join(str(payload[k]) for k in keys if payload.get(k))
                if text:
                    return text[:200]
    return fallback


def _tokens_from(payload: dict) -> Tokens:
    return Tokens(
        access_token=str(payload.get("access_token", "")),
        refresh_token=str(payload.get("refresh_token", "")),
        expires_in=int(payload.get("expires_in", 0) or 0),
    )


# —— the client ————————————————————————————————————————————————————————


class CloudClient:
    """Thin HTTPS wrapper over the Pulse cloud Worker (worker-auth +
    PostgREST-style). The class was once named SupabaseClient because the
    Worker deliberately keeps Supabase's URL/JSON shapes; the name is
    honest now. Every method returns AuthResult/dict, never raises for
    HTTP-level problems, and never logs tokens.
    """

    def __init__(
        self,
        base_url: str,
        publishable_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 8.0,
    ):
        self._base = base_url.rstrip("/")
        self._key = publishable_key
        # the publishable key ALWAYS goes as apikey; the Bearer token is
        # only attached per-request when the call is user-scoped
        self._http = httpx.Client(
            base_url=self._base,
            headers={"apikey": publishable_key},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # —— auth ———————————————————————————————————————————————————————————
    def sign_up(
        self,
        email: str,
        password: str,
        redirect_to: str = "",
        code_challenge: str = "",
    ) -> AuthResult:
        """Create an account. With confirm-email ON the server stores the
        PKCE challenge for THIS signup and returns NO tokens; the verify
        link later redirects with a code bound to our parked verifier."""
        email, password = email.strip(), password
        try:
            r = self._http.post(
                "/auth/v1/signup",
                json={
                    "email": email,
                    "password": password,
                    # with confirm-email ON, the challenge binds the LATER
                    # verification-link code exchange to our parked verifier
                    **({"code_challenge": code_challenge} if code_challenge else {}),
                    **({"code_challenge_method": "s256"} if code_challenge else {}),
                },
                params={"redirect_to": redirect_to} if redirect_to else None,
            )
        except httpx.HTTPError:
            return AuthResult(error="network error reaching the auth server")
        if r.status_code >= 400:
            return AuthResult(error=_parse_error(r.json(), "sign-up failed"))
        body = r.json()
        if not body.get("access_token"):
            # user created; confirmation email in flight
            return AuthResult(
                user=body.get("user") or {}, needs_email_confirmation=True
            )
        return AuthResult(
            ok=True, tokens=_tokens_from(body), user=body.get("user") or {}
        )

    def sign_in_with_password(self, email: str, password: str) -> AuthResult:
        email, password = email.strip(), password
        try:
            r = self._http.post(
                "/auth/v1/token",
                params={"grant_type": "password"},
                json={"email": email, "password": password},
            )
        except httpx.HTTPError:
            return AuthResult(error="network error reaching the auth server")
        if r.status_code >= 400:
            return AuthResult(error=_parse_error(r.json(), "sign-in failed"))
        body = r.json()
        return AuthResult(
            ok=True, tokens=_tokens_from(body), user=body.get("user") or {}
        )

    def refresh(self, refresh_token: str) -> AuthResult:
        try:
            r = self._http.post(
                "/auth/v1/token",
                params={"grant_type": "refresh_token"},
                json={"refresh_token": refresh_token},
            )
        except httpx.HTTPError:
            return AuthResult(
                error="network error refreshing the session", network_error=True
            )
        if r.status_code >= 400:
            # invalid/expired refresh token: the caller should forget the session
            return AuthResult(error=_parse_error(r.json(), "session expired"))
        body = r.json()
        return AuthResult(
            ok=True, tokens=_tokens_from(body), user=body.get("user") or {}
        )

    def exchange_pkce_code(self, code: str, code_verifier: str) -> AuthResult:
        """Finish the browser/OAuth flow (authorization code → session)."""
        try:
            r = self._http.post(
                "/auth/v1/token",
                params={"grant_type": "pkce"},
                json={"auth_code": code, "code_verifier": code_verifier},
            )
        except httpx.HTTPError:
            return AuthResult(error="network error exchanging the auth code")
        if r.status_code >= 400:
            return AuthResult(error=_parse_error(r.json(), "auth code rejected"))
        body = r.json()
        return AuthResult(
            ok=True, tokens=_tokens_from(body), user=body.get("user") or {}
        )

    def oauth_authorize_url(
        self, provider: str, redirect_to: str, code_challenge: str
    ) -> str:
        """Return the URL to open in the system browser (no request needed)."""
        params = httpx.QueryParams(
            {
                "provider": provider,
                "redirect_to": redirect_to,
                "code_challenge": code_challenge,
                "code_challenge_method": "s256",
            }
        )
        return f"{self._base}/auth/v1/authorize?{params}"

    def recover(
        self, email: str, redirect_to: str = "", code_challenge: str = ""
    ) -> AuthResult:
        """Send the 'reset password' email (link lands on redirect_to).
        With PKCE, the challenge is bound here and the link later
        redirects with a code the parked verifier can exchange."""
        try:
            r = self._http.post(
                "/auth/v1/recover",
                json={
                    "email": email.strip(),
                    **({"code_challenge": code_challenge} if code_challenge else {}),
                    **({"code_challenge_method": "s256"} if code_challenge else {}),
                },
                params={"redirect_to": redirect_to} if redirect_to else None,
            )
        except httpx.HTTPError:
            return AuthResult(error="network error sending the reset email")
        if r.status_code >= 400:
            return AuthResult(
                error=_parse_error(r.json(), "could not send reset email")
            )
        return AuthResult(ok=True)

    def logout(self, access_token: str) -> None:
        """Best-effort server-side revocation; no useful return value."""
        try:
            self._http.post(
                "/auth/v1/logout",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError:
            pass

    def user(self, access_token: str) -> AuthResult:
        try:
            r = self._http.get(
                "/auth/v1/user", headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx.HTTPError:
            return AuthResult(error="network error fetching the profile")
        if r.status_code >= 400:
            return AuthResult(error=_parse_error(r.json(), "session rejected"))
        return AuthResult(ok=True, user=r.json() or {})

    # ── updates (v1.2.0) ─────────────────────────────────────────────────
    def latest_release(
        self, access_token: str, current_version: str
    ) -> tuple[int, dict]:
        """GET /updates/latest — Bearer-gated, registered+active accounts.

        Returns (status, payload). Status 0 = offline sentinel, 401 = the
        session was refused (caller may force one refresh + retry),
        403 = account outside the activity window. Payload is untrusted:
        `cloud.updates.trust` verifies the Ed25519 manifest signature
        before `policy.evaluate` acts on it.
        """
        try:
            r = self._http.get(
                "/updates/latest",
                params={"version": current_version},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError:
            return 0, {}
        try:
            body: dict = r.json() if isinstance(r.json(), dict) else {}
        except ValueError:
            body = {}
        return r.status_code, body

    # —— PostgREST (cloud tables; RLS enforces ownership) ——————————————
    def rest_select(
        self, table: str, query: str, access_token: str
    ) -> tuple[int, list | dict]:
        """SELECT via REST. Returns (status, json). auth errors surface as 401."""
        return self._rest("GET", f"/rest/v1/{table}?{query}", access_token, json=None)

    def rest_upsert(
        self, table: str, rows: list[dict], access_token: str
    ) -> tuple[int, list | dict]:
        return self._rest(
            "POST",
            f"/rest/v1/{table}",
            access_token,
            json=rows,
            extra={"Prefer": "resolution=merge-duplicates,return=representation"},
        )

    def rest_delete(
        self, table: str, query: str, access_token: str
    ) -> tuple[int, list | dict]:
        return self._rest(
            "DELETE", f"/rest/v1/{table}?{query}", access_token, json=None
        )

    def _rest(
        self,
        method: str,
        url: str,
        access_token: str,
        json=None,
        extra: dict | None = None,
    ) -> tuple[int, list | dict]:
        headers = {"Authorization": f"Bearer {access_token}", **(extra or {})}
        try:
            r = self._http.request(method, url, json=json, headers=headers)
        except httpx.HTTPError:
            return 0, {"message": "network error"}
        try:
            body: list | dict = r.json()
        except ValueError:
            body = {}
        return r.status_code, body
