# Pulse-HWM update system — design + runbook

An in-app updater is, by definition, a **remote-code-execution path**: it
downloads and runs code. This document explains how Pulse-HWM keeps that
path safe, and how to operate it — publishing a release, rotating keys,
and smoke-testing the pipeline.

## How it works (end to end)

```
push tag v1.2.1
 └─ GitHub Actions release.yml (windows-latest; actions pinned by SHA)
      guard: tag == pulse_hwm.__version__ == installer/version.iss
      PyInstaller → Inno Setup → PulseHWM-Setup-1.2.1.exe
      SHA-256 → Ed25519-sign manifest (CI secret only) → SHA256SUMS
      GitHub release (installer only) → R2 upload → POST /updates/publish

signed-in + ACTIVE app polls GET /updates/latest (Bearer)
   █ manifest sig verified against the embedded public key (fail-closed)
   █ strictly-newer + anti-rollback + host allowlist
   toast + ACCOUNT banner with [INSTALL NOW]
   █ GET /dl/<asset> (Bearer-gated, private R2 via the worker)
   █ SHA-256 == signed value; Authenticode (WinVerifyTrust) gates the file
   █ run Setup.exe /SILENT /NORESTART /LAUNCHAFTER=1 → app quits →
      installer replaces files → relaunches Pulse
```

Everyone without a session sees only the ACCOUNT-tab CTA
("LOG IN TO GET THE LATEST OF PULSE — INCLUDING SECURITY FIXES").

## Trust model (why this is safe)

| Threat                              | Control |
| ----------------------------------- | ------- |
| tampered/ CDN-mitm download         | TLS + SHA-256 bound to the SIGNED manifest |
| compromised cloud worker            | Ed25519 key lives ONLY in CI — the worker can't forge updates |
| signed old release (replay/freeze)  | client refuses anything not strictly newer than the highest-seen version; server anti-downgrade 409 |
| compromised download host           | exact-host allowlist (worker host + GitHub) |
| downgrade past a security fix       | `min_supported` floor → forced update ("SECURITY UPDATE" banner, no LATER) |
| unsigned/modified installer on disk | pre-spawn re-hash + WinVerifyTrust (Authenticode hard-required once signing is live) |
| dormant accounts burning tokens     | activity window `UPDATE_ACTIVITY_DAYS` (default 90) throttled writes |

Client trust anchors: `pulse_hwm/cloud/updates/trust.py` (`TRUSTED_UPDATE_KEYS`,
`AUTHENTICODE_REQUIRED`). Server validation: `workers/src/routes/updates.js`.

## One-time setup checklist

1. **R2 bucket** (front desk file locker):
   `npx wrangler r2 bucket create pulsehwm-releases` — binding `BUCKET` is
   already declared in `workers/wrangler.jsonc`.
2. **D1 schema** (idempotent):
   `npx wrangler d1 execute pulsehwm-data --remote --file=schema.sql` (cwd `workers/`)
3. **Worker secret**: `npx wrangler secret put RELEASE_KEY` (long random string).
4. **Ed25519 keypair**: `python scripts/update_signing.py gen` → paste the
   `public_hex` into `pulse_hwm/cloud/updates/trust.py`
   (`TRUSTED_UPDATE_KEYS`); put `private_hex` into the GitHub secret
   `UPDATE_SIGNING_KEY`. Never email/commit/chat the private key.
5. **GitHub Actions secrets** (Settings → Secrets → Actions):
   `UPDATE_SIGNING_KEY`, `RELEASE_KEY`, `CLOUDFLARE_API_TOKEN`,
   `CLOUDFLARE_ACCOUNT_ID`.
6. **SignPath (optional, recommended)**: create the OSS project → sign the
   installer in CI → flip `AUTHENTICODE_REQUIRED = True` in trust.py once
   every release ships signed. Until then the Ed25519 + SHA-256 pair is the
   binding identity.
7. Enable **branch protection** on `main` (CI green + review).

## Publishing a release (the routine)

1. Update `pulse_hwm/__init__.py` `__version__` and add the matching
   `## [x.y.z]` section to `CHANGELOG.md` (this text becomes BOTH the GitHub
   release body AND the in-app banner — write it for users).
2. Commit on a feature branch, merge to `main` (CI green).
3. Tag & push: `git tag v1.2.1 && git push origin v1.2.1`.
4. CI builds/attaches the installer, uploads it to R2, publishes metadata.
   Nothing else to do — installs update themselves within ~6 h (or on
   next launch / manual tray check).

## Smoke tests (curl)

```powershell
# 1. publish (as CI would)
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
