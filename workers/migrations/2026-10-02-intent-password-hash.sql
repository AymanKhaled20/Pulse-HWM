-- Security fix (2026-10-02): signup passwords ride on the emailed code
-- instead of the unconfirmed user row (account pre-hijack fix).
--
-- Apply ONCE to the live database BEFORE deploying the matching worker:
--   npx wrangler d1 execute pulsehwm-data --remote --file=migrations/2026-10-02-intent-password-hash.sql
-- (schema.sql already includes the column for fresh databases.)

ALTER TABLE intent_codes ADD COLUMN password_hash TEXT;

-- Unconfirmed rows may hold a password typed by someone who never proved
-- they own the email. Clear them: the real owner sets theirs when they
-- confirm, and confirmed accounts are untouched.
UPDATE users SET password_hash = NULL WHERE email_verified = 0;
