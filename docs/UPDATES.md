# Pulse-HWM update system — design + runbook

Follow **Part A** top-to-bottom ONCE; after that, publishing a release is
just "bump the version + CHANGELOG, push a tag" (Part B).

An in-app updater is, by definition, a **remote-code-execution path** —
it downloads and runs code. This document explains how Pulse-HWM keeps that
path safe, and how to operate it.

## How it works (end to end, 30-second version)

```
YOU push tag v1.2.1
  └─ GitHub Actions release.yml builds the Windows installer,
     signs its metadata, uploads it to storage, and tells the cloud
     cloud ── registered + ACTIVE users' Pulse apps poll the cloud
       they see a toast + [INSTALL NOW] on the ACCOUNT tab
       app silently downloads, verifies (signature + hash + Windows check),
       runs the installer and relaunches itself
```

Everyone without a session sees only the ACCOUNT-tab CTA
("LOG IN TO GET THE LATEST OF PULSE — INCLUDING SECURITY FIXES").
Update binaries are members-only end to end.

## Trust model (why this is safe)

| Threat                              | Control |
| ----------------------------------- | ------- |
| tampered/men-in-the-middle download | TLS + SHA-256 bound to the SIGNED manifest |
| compromised cloud worker            | Ed25519 signing key lives ONLY in CI — the worker can't forge updates |
| replayed old signed release         | client refuses anything not strictly newer than the highest-seen version; server anti-downgrade 409 |
| malicious download host             | exact-host allowlist (worker host + GitHub) |
| downgrade past a security fix       | `min_supported` floor → forced update ("SECURITY UPDATE" banner, no LATER) |
| unsigned/modified installer on disk | pre-spawn re-hash + WinVerifyTrust (Authenticode hard-required once signing is live) |

Client trust anchors: `pulse_hwm/cloud/updates/trust.py`
(`TRUSTED_UPDATE_KEYS`, `AUTHENTICODE_REQUIRED`).
Server validation: `workers/src/routes/updates.js`.

---

# PART A — one-time setup (do once, ~15 min of actual work)

Prerequisite: `npx wrangler login` works and the `workers/` folder is your
cwd for anything wrangler-related. Every command below is PowerShell.

> **No credit card? No problem.** R2 is optional: by default releases ship
> on **GitHub release assets** (the client's built-in fallback) and only
> the `UPDATE_SIGNING_KEY` + `RELEASE_KEY` secrets are mandatory. R2
> (private member-gated streaming) can be switched on later by enabling R2
> in the dashboard (needs a payment card on file even on the free tier),
> creating the bucket, un-commenting the binding in `wrangler.jsonc`,
> redeploying, and setting the repo variable `R2_ENABLED=true` — zero app
> changes needed.

## Step 1 — Deploy the new worker code + database schema ✅ DONE (2026-09-15)

The old deployed worker is still the single-file monolith. Deploy the
new modular one, then run the schema (it only ADDS two tables; it is
idempotent, so it is safe to repeat).

```powershell
cd workers
npx wrangler deploy
npx wrangler d1 execute pulsehwm-data --remote --file=schema.sql
```

✅ Check: `curl.exe https://pulsehwm-cloud.pulsehwm27.workers.dev/` prints
`{"app":"PulseHWM cloud","ok":true}`.

## Step 2 — Create the R2 bucket (private file locker) — SKIP for now (optional)

This is where the 63 MB installers live. The bucket is private — nobody
can download from it directly; your worker streams files out of it only
to signed-in, active accounts.

```powershell
npx wrangler r2 bucket create pulsehwm-releases
```

✅ Check: it appears at dash.cloudflare.com → R2 → Object Storage.

## Step 3 — RELEASE_KEY  ✅ DONE — only the GitHub half remains

One random string, used in two places. CI will send it when publishing a
release; the worker compares it before accepting.

**Already done:** the key was generated and set on the worker via the
Cloudflare API (✅). The copy you must paste into GitHub lives in
`%TEMP%\opencode\release_keys_new.txt` — that value goes into the
`RELEASE_KEY` secret in step 5 (they must be the SAME string).

## Step 4 — Ed25519 signing keypair  ✅ PARTLY DONE — only the GitHub secret remains

Why: the cloud must never be ABLE to forge an update. The signing half
lives only in GitHub Actions; the matching public half is baked into the
app so every install can verify releases independently of the server.

(Already generated on 2026-09-15 — skip the command.)

It printed two values:

- **`private_hex`** → you will paste this into GitHub (step 5).
  Never commit, email or chat it.
- **`public_hex`** → already embedded in
  `pulse_hwm/cloud/updates/trust.py` (`TRUSTED_UPDATE_KEYS`) and committed ✅
  (rebuild the exe so it is baked in).

## Step 5 — GitHub Actions secrets

Repo page → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret**. Add the first two (mandatory); the Cloudflare
pair is only needed once R2 is enabled:

| Name | Value |
|---|---|
| `UPDATE_SIGNING_KEY` | the `private_hex` from step 4 |
| `RELEASE_KEY` | the same string as step 3 |
| `CLOUDFLARE_API_TOKEN` | (optional — only for R2) dash.cloudflare.com → My Profile → API Tokens → template "Edit Cloudflare Workers" (plus R2 edit permission) |
| `CLOUDFLARE_ACCOUNT_ID` | (optional — only for R2) your 32-char account ID, visible in any Cloudflare dashboard URL |

## Step 6 — SignPath code signing (OPTIONAL, do later if you like)

Free code signing for open source (removes the SmartScreen warning on the
installer). Apply at signpath.org, create a "Pulse-HWM" project, add the
API token as the GitHub secret `SIGNPATH_API_TOKEN`, and uncomment the
SignPath block in `.github/workflows/release.yml`. Then flip
`AUTHENTICODE_REQUIRED = True` in `trust.py` once every release ships
signed. Until then the Ed25519 + SHA-256 pair carries the security.

## Step 7 — Branch protection + rebuild the app

- Repo → Settings → Branches → add rule for `main`: require the `ci`
  workflow + one review.
- Rebuild the app so it embeds the new public key from step 4:
  `.venv\Scripts\pyinstaller.exe pulse_hwm.spec --noconfirm`

## One-time steps you may skip (already done in code)

- `BUCKET` binding, `GITHUB_REPO`, `UPDATE_ACTIVITY_DAYS` — already declared
  in `workers/wrangler.jsonc`.
- Client endpoints, policy, trust logic — already in the repo and tested.

---

# PART B — Publishing a release (the routine)

1. Bump `__version__` in `pulse_hwm/__init__.py` (the ONLY version source;
   CI generates `installer/version.iss` and fails the build if they drift).
2. Add the matching `## [x.y.z]` section to `CHANGELOG.md` — that text
   becomes BOTH the GitHub release body AND the in-app update banner,
   so write it for users.
3. Merge to `main` (branch protection + CI must be green).
4. Tag & push:
   ```powershell
   git tag v1.2.1
   git push origin v1.2.1
   ```
   That is the whole job: CI builds the installer, uploads it to R2 and
   publishes metadata; member installs update themselves within ~6 h
   (or on next launch / manual tray "CHECK FOR UPDATES").

## Smoke tests (curl)

```powershell
# 1. publish (exactly what CI would send)
$json = '{"version":"1.2.1","sha256":"<(certutil -hashfile setup.exe SHA256)>",
  "manifest":"<exact manifest string>","manifest_sig":"<sig>","published_at":"2026-09-20T00:00:00+00:00"}'
curl.exe -X POST https://pulsehwm-cloud.pulsehwm27.workers.dev/updates/publish `
  -H "x-pulse-release-key: $env:RELEASE_KEY" -H "content-type: application/json" -d $json

# 2. metadata (needs a bearer token from a real sign-in)
curl.exe -H "Authorization: Bearer $tok" `
  "https://pulsehwm-cloud.pulsehwm27.workers.dev/updates/latest?version=1.2.0"

# 3. binary (Bearer; Range-capable)
curl.exe -H "Authorization: Bearer $tok" -o out.exe `
  https://pulsehwm-cloud.pulsehwm27.workers.dev/dl/PulseHWM-Setup-1.2.1.exe
```

Expected gates: no/invalid token → 401; inactive account → 403
("account inactive — sign in again"); older publish attempt → 409
anti-downgrade; unsigned metadata → client refuses (no install, event logged).

## Bootstrap note

v1.2.0 is the first build that SHIPS the updater, so nobody can be
auto-notified about v1.2.0 itself: users on ≤1.1.6 install it manually.
**v1.2.1 is the first version anybody can be auto-notified about** — so it is
also the natural live test of the whole pipeline.

