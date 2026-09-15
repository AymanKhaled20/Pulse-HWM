# Security Policy

Pulse-HWM is a desktop hardware/website monitor with cloud sync and an
in-app update system. Because the updater is code-execution infrastructure,
security reports are taken seriously and treated with priority.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.2.x   | yes       |
| < 1.2.0 | partial (no update channel; manual installs only) |

## Reporting a vulnerability

**Do NOT open a public GitHub issue for security problems.**

Email **aymankahled27@gmail.com** with:

- affected version (Help → About, or the exe's `PulseHWM-Setup-<v>` name),
- what you observed (logs, `error.log` excerpt, screenshots),
- reproduction steps, impact, and (if known) a suggested fix.

You will get an acknowledgement within **72 hours**. We aim for a fix
release within 30 days for high-severity issues; you will be credited in
the changelog unless you prefer to stay anonymous.

## Submissions we consider in scope

- The update pipeline (`pulse_hwm/cloud/updates/*`,
  `workers/src/routes/updates.js`, `routes/download.js`) — e.g. ways to
  serve, downgrade, or forge an update, or bypass the signature/hash gates.
- The auth/session model (`workers/src/*`, `pulse_hwm/cloud/session.py`,
  `token_store.py`) — token handling, PKCE, refresh-rotation families.
- The Worker's ownership fencing (`/rest/v1/*` cross-user access).
- Secret handling (anything that could leak webhook URLs, tokens, or keys
  into logs, files, crash dumps, or outgoing messages).

## Out of scope

- Vulnerabilities in third-party libraries (report upstream; we run
  `pip-audit` and Dependabot in CI and will track fixes).
- Local attacks that require the victim's unlocked Windows session
  (malware compromise is out of threat model).
- The public marketing pages.

## Hardening facts (for reviewers)

- All cloud secrets live in Cloudflare Worker env, never in-code; the
  client embeds publishable/verification material only.
- Refresh tokens live in Windows Credential Manager (DPAPI), rotate on
  every use, and a replayed rotated token kills the whole token family.
- Pre-commit scanners (`scripts/scan_secrets.py`, `scripts/gitleaks_guard.py`)
  and CI (`pip-audit`, gitleaks) block secret leakage.
- Update-trust story: see `docs/UPDATES.md`.
