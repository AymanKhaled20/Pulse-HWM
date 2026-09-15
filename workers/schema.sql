-- PulseHWM cloud schema (Cloudflare D1 = SQLite).
-- Apply with: npx wrangler d1 execute pulsehwm-data --remote --file=schema.sql
-- Idempotent-ish: IF NOT EXISTS everywhere; fresh projects run clean.

CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,            -- 'u' + 32 hex
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT,                        -- PBKDF2-SHA256(10k), salt$hash; NULL = OAuth-only
    provider       TEXT NOT NULL DEFAULT 'password',  -- signup origin
    email_verified INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

-- refresh token ROTATION FAMILIES: children share a family_id; if a
-- retired (already-rotated) token is replayed, the whole family dies.
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,   -- sha-256 hex of the raw token
    user_id    TEXT NOT NULL REFERENCES users(id),
    family_id  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rtf_ondfamily ON refresh_tokens(family_id);

-- one-time signup/verification intents (email link → pulsehwm:// code)
CREATE TABLE IF NOT EXISTS intent_codes (
    code           TEXT PRIMARY KEY,          -- random, emailed via worker redirect
    user_id        TEXT NOT NULL REFERENCES users(id),
    code_challenge TEXT NOT NULL,             -- PKCE S256 challenge parked at signup
    kind           TEXT NOT NULL,             -- 'signup-verify'
    expires_at     TEXT NOT NULL
);

-- in-flight OAuth: desktop parks challenge, browser comes back with state
CREATE TABLE IF NOT EXISTS oauth_states (
    state          TEXT PRIMARY KEY,          -- sent to Google/GitHub
    provider       TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    app_code       TEXT NOT NULL,             -- handed to the desktop at callback
    expires_at     TEXT NOT NULL
);

-- per-user sync tables (ownership enforced by the worker = the RLS stand-in)
CREATE TABLE IF NOT EXISTS user_settings (
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,   -- unix epoch seconds TEXT to match client
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS user_sites (
    user_id         TEXT NOT NULL,
    site_uuid       TEXT NOT NULL,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    method          TEXT NOT NULL DEFAULT 'GET',
    timeout_s       REAL NOT NULL DEFAULT 10.0,
    expected_status INTEGER NOT NULL DEFAULT 200,
    keyword         TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    deleted         INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, site_uuid)
);

-- Update channel (v1.2.0). CI publishes a row on every GitHub release;
-- only REGISTERED + ACTIVE users may read it or download the asset.
-- manifest is the EXACT string Ed25519-signed by CI — the desktop app
-- verifies the signature over the raw string (embedded public key), so
-- no JSON canonicalization is ever needed.
CREATE TABLE IF NOT EXISTS releases (
    version        TEXT PRIMARY KEY,          -- X.Y.Z, strictly increasing
    channel        TEXT NOT NULL DEFAULT 'stable',
    notes          TEXT NOT NULL DEFAULT '',  -- CHANGELOG section (shown in-app)
    html_url       TEXT NOT NULL DEFAULT '',  -- GitHub release page
    asset_name     TEXT NOT NULL,             -- PulseHWM-Setup-<version>.exe
    sha256         TEXT NOT NULL,             -- of the installer binary
    manifest       TEXT NOT NULL DEFAULT '',  -- signed JSON (version, asset, sha256)
    manifest_sig   TEXT NOT NULL DEFAULT '',  -- Ed25519(b64url) over manifest
    published_at   TEXT NOT NULL,
    min_supported  TEXT NOT NULL DEFAULT '',  -- security floor; below = forced
    mandatory      INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

-- Per-account update entitlement telemetry. last_seen_at is touched
-- (throttled ≥12h between writes) on authenticated activity; accounts
-- outside UPDATE_ACTIVITY_DAYS get 403 on /updates/* + /dl/*.
CREATE TABLE IF NOT EXISTS user_update_state (
    user_id           TEXT PRIMARY KEY,
    last_seen_at      TEXT NOT NULL,
    last_check_at     TEXT NOT NULL DEFAULT '',
    last_seen_version TEXT NOT NULL DEFAULT ''
);
