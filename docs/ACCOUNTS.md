# Accounts & Cloud Sync (v1.1.0 runbook)

## What the feature is
Optional accounts. The app is fully functional signed out; accounts add
**cloud sync of sites + portable settings** (per-key last-write-wins).
Login flows: email/password, Google, GitHub (PKCE, custom scheme
`pulsehwm://auth-callback`). The refresh token lives in Windows Credential
Manager via `keyring` — never in files, never in the repo.

## Architecture in one breath
Desktop app ──HTTPS──► Supabase (Auth + PostgREST). Tables
`profiles` / `user_settings` / `user_sites` — RLS fences every row to
`auth.uid()`. Only the PUBLISHABLE key ships inside the exe; the
service-role key never leaves the Supabase dashboard.

## Manual setup (one-time, ~15 minutes)
1. **Supabase project** → https://supabase.com/dashboard
   - Create org "Pulse-HWM" + free project.
   - Copy Project URL and the `sb_publishable_...` key.
   - Put both into `.env` (dev) as `SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY`.
2. **Apply the schema** → SQL editor:
   - paste `supabase/migrations/0001_tables_rls.sql`, run;
   - paste `supabase/migrations/0002_profile_trigger.sql`, run.
3. **Brevo SMTP** (free 300/day) → https://app.brevo.com
   - Sign up, verify sender → https://app.brevo.com/senders
   - Create SMTP key → https://app.brevo.com/settings/keys/smtp
   - **IMPORTANT: Brevo's SMTP LOGIN is NOT your account email** — it is a
     special address shown on that page (`<id>@smtp-brevo.com`). Use THAT as
     the Supabase SMTP username; the SMTP key is the password.
   - Supabase → Auth → SMTP: host `smtp-relay.brevo.com`, port 587,
     user = the `@smtp-brevo.com` login, pass = SMTP key, sender =
     the verified sender address.
4. **Redirect URLs** → Auth → URL Configuration: add exact
   `pulsehwm://auth-callback` (+ `http://localhost:53124/**` for dev tests).
5. **Google OAuth** → https://console.cloud.google.com/apis/credentials
   - OAuth client, type *Web application*;
   - Authorized redirect: `https://<ref>.supabase.co/auth/v1/callback`;
   - paste client id/secret into Supabase → Auth → Providers → Google.
6. **GitHub OAuth App** (under the org):
   https://github.com/organizations/<ORG>/settings/applications/new
   - callback: `https://<ref>.supabase.co/auth/v1/callback`;
   - paste into Supabase → Auth → Providers → GitHub.
7. **Keepalive secrets** (freely-visible values, still use secrets):
   - https://github.com/AymanKhaled20/Pulse-HWM/settings/secrets/actions
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY` (publishable key).
8. **Dev**: register the scheme for local testing (no installer):
   ```powershell
   New-Item -Path "HKCU:\Software\Classes\pulsehwm\shell\open\command" -Force
   Set-ItemValue HKCU:\Software\Classes\pulsehwm "(Default)" "URL:Pulse-HWM Auth Protocol"
   Set-ItemProperty HKCU:\Software\Classes\pulsehwm -Name "URL Protocol" -Value ""
   Set-ItemValue HKCU:\Software\Classes\pulsehwm\shell\open\command "(Default)" '"C:\path\to\PulseHWM.exe" "%1"'
   ```
   (the installed exe writes these keys automatically via Inno Setup)

## Testing the flows
- `pytest` covers auth/merge/token/PKCE offline (no network in tests).
- Manual smoke: sign up (email confirm via Brevo), sign in, add a site
  on machine A, sign in on machine B, SYNC NOW on B → the site arrives.

## Security posture summary
TLS-only · PKCE (no client secret in the exe) · refresh-token revocation on
logout · RLS on every table (anon key harmless) · generic auth errors ·
tokens redacted from logs (we never print them) · single-use verifiers with
a 15-minute TTL · device-specific settings never leave the machine.

## Privacy (plain talk)
We store: your email/account id, your saved sites + portable preferences.
Everything else (hardware stats, processes, checks history) NEVER leaves the
machine. Delete the account in Supabase to wipe cloud data; local data stays
yours.
