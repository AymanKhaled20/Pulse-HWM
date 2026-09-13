from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# ── PUBLIC values, not secrets ─────────────────────────────────────────
# The Worker URL + client key are public client values (ownership is
# enforced server-side by the Worker; RLS stand-in). They ship inside
# the exe so ANY fresh install works with zero configuration; .env
# still overrides for dev builds / pointing at a different project.
#
# NEVER put Worker secrets (JWT key, Brevo key, OAuth secrets) anywhere
# in the repo — they live only in the Worker via `wrangler secret put`.

# filled after `npx wrangler deploy` + `wrangler secret put` step
# (placeholder keeps builds working; empty = accounts disabled)
DEFAULT_BASE_URL = "https://pulsehwm-cloud.workers.dev"

DEFAULT_PUBLISHABLE_KEY = "pulsehwm-public"


@dataclass(frozen=True)
class AuthConfig:
    """Public, non-secret cloud config. Empty values = accounts disabled."""

    base_url: str = ""
    publishable_key: str = ""

    def is_configured(self) -> bool:
        return bool(self.base_url and self.publishable_key)


def auth_config() -> AuthConfig:
    load_dotenv(override=False)
    return AuthConfig(
        base_url=os.environ.get("CLOUD_URL", "").strip().rstrip("/")
        or DEFAULT_BASE_URL,
        publishable_key=os.environ.get("CLOUD_CLIENT_KEY", "").strip()
        or DEFAULT_PUBLISHABLE_KEY,
    )
