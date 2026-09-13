# Pulse-HWM Accounts + Cloud Sync (Cloudflare Workers edition)

The desktop app needs ZERO configuration — the public Worker URL and
client key are embedded in every build (they are not secrets; ownership
is enforced server-side, like Supabase's RLS used to be).

## How it's built
- **Cloudflare Worker** (`workers/src/worker.js`) speaks the same URL paths
  and JSON shapes the Python client expects (`/auth/v1/*`, `/rest/v1/*`),
  so `pulse_hwm/auth/` only swaps its base URL.
- **D1 database** (`workers/schema.sql`) = SQLite: users, refresh-token
  families, intent codes (email links / OAuth app codes), oauth states,
  and per-user `user_settings` / `user_sites`.
- **Emails** via the Brevo HTTP API (raw SMTP doesn't exist from a
  Worker). Link click → browser → `pulsehwm://auth-callback`.
- **PKCE intact**: the desktop parks a verifier in Credential Manager;
  the Worker stores the matching challenge and rejects exchanged codes
  whose S256(verifier) doesn't match. Refresh tokens rotate with family
  revocation on replay.

## Deploying / updating the Worker
```powershell
cd workers
npx wrangler login                 # one-time browser login
npx wrangler d1 create pulsehwm-data   # copy the database_id into wrangler.jsonc
npx wrangler d1 execute pulsehwm-data --remote --file=schema.sql
npx wrangler secret put JWT_SECRET      # long random string
npx wrangler secret put HASH_PEPPER     # long random string (password pepper)
npx wrangler secret put BREVO_API_KEY   # Brevo → SMTP & API key page
npx wrangler secret put SENDER_EMAIL    # verified sender (aymankahled27@gmail.com)
npx wrangler secret put SENDER_NAME     # "Pulse-HWM"
npx wrangler secret put GOOGLE_CLIENT_SECRET   # from console.cloud.google.com
npx wrangler secret put GITHUB_CLIENT_SECRET   # from github.com/settings/developers
npx wrangler deploy
```
After the first deploy, put the real URL + client key into
`pulse_hwm/auth/config.py` (DEFAULT_BASE_URL / DEFAULT_PUBLISHABLE_KEY)
and rebuild the exe so fresh installs work offline of `.env`.

GOOGLE_CLIENT_ID / GITHUB_CLIENT_ID are public ids → `vars` in
`workers/wrangler.jsonc` (not secrets). Non-secret Brevo values also
belong there if you'd rather not use secrets.

## Redirect URLs to register (done during the v1.1.1 deploy)
- **Google OAuth client** (`905085029470-…`):
  `https://pulsehwm-cloud.pulsehwm27.workers.dev/cb/google`
- **GitHub OAuth app** (`Ov23liy6BmcDeKrLQHKK`):
  `https://pulsehwm-cloud.pulsehwm27.workers.dev/cb/github`
- **Desktop deep link** (unchanged): `pulsehwm://auth-callback` —
  registered by the installer (HKCU registry keys) or, for dev runs of
  `python -m pulse_hwm` on other machines, by the same two PowerShell
  commands below:
  ```powershell
  New-Item -Path "HKCU:\Software\Classes\pulsehwm\shell\open\command" -Force
  Set-ItemProperty HKCU:\Software\Classes\pulsehwm "(Default)" "URL:Pulse-HWM Auth Protocol"
  Set-ItemProperty HKCU:\Software\Classes\pulsehwm -Name "URL Protocol" -Value ""
  Set-ItemProperty HKCU:\Software\Classes\pulsehwm\shell\open\command "(Default)" '"C:\path\to\PulseHWM.exe" "%1"'
  ```
  (the installed exe writes these keys automatically via Inno Setup)

## Testing the flows
- `pytest` covers auth/merge/token/PKCE offline (no network in tests).
- Real-machine verification: deploy the Worker → build the exe →
  sign-up with email (Brevo message arrives, link opens the installed exe)
  → sites push; sign in on a second machine → SYNC NOW.

## Why Cloudflare over Supabase
- No free-tier pausing (Workflows/D1 free tier doesn't hibernate).
- Emails: Brevo when configured; without it, signup issues tokens
  immediately (dev/self-host mode). 300/day free remains the practical cap.
- Free D1 caps: 500 MB, 5M rows read/day — private-sync usage is nowhere near.
- Everything is small, versioned, and auditable in `workers/`.

## Deployment facts (2026-09-13, initial go-live)
- Worker `pulsehwm-cloud`, URL `https://pulsehwm-cloud.pulsehwm27.workers.dev`
  (subdomain `pulsehwm27`), D1 `pulsehwm-data` in region EEUR, schema applied.
- All secrets set via `wrangler secret put`; client IDs live in `vars`
  inside `wrangler.jsonc`. `workers/.dev.vars` mirrors them locally
  (gitignored) for `wrangler dev`.
- Smoke-tested live: signup (Brevo email sent), PKCE exchange with verifier
  check, password grant (wrong password rejected), `/rest/v1` upsert + fenced
  read (foreign user 403), refresh rotation, replay-kill of the token family.
- Google + GitHub OAuth button flows verified up to the provider consent
  page; complete one real sign-in per provider on a normal browser window
  (the debugged Brave tab freezes provider consent) to finish that check.
