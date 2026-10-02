-- Age gate (2026-10-02): new accounts require the app's 16+ confirmation.
--
-- Apply ONCE to the live database BEFORE deploying the matching worker,
-- after 2026-10-02-intent-password-hash.sql:
--   npx wrangler d1 execute pulsehwm-data --remote --file=migrations/2026-10-02-age-gate.sql
-- (schema.sql already includes both columns for fresh databases.)
--
-- Existing accounts keep age_confirmed_at NULL: they signed up before
-- the gate existed.

ALTER TABLE users ADD COLUMN age_confirmed_at TEXT;
ALTER TABLE oauth_states ADD COLUMN age_confirmed INTEGER NOT NULL DEFAULT 0;
