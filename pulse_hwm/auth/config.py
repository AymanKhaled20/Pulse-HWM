from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# ── PUBLIC values, not secrets ─────────────────────────────────────────
# Supabase URLs + publishable keys are designed to ship inside clients;
# Row Level Security is the actual security boundary. They still come
# from .env so a dev build can point at a different project, and so the
# secret scanners never see anything resembling a key in source code.
#
# NEVER put the service_role key here (or anywhere else in the repo).


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
        base_url=os.environ.get("SUPABASE_URL", "").strip().rstrip("/"),
        publishable_key=os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip(),
    )
