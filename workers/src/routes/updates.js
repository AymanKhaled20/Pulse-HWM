// Update channel: publishes release metadata (from CI) and serves the
// newest release to REGISTERED + ACTIVE users of the desktop app.
//
// Threat notes:
// - The publish secret (env.RELEASE_KEY) maps 1:1 to CI — comparing
//   SHA-256 digests keeps the comparison effectively constant-time.
// - The signed manifest travels as the EXACT string CI signed
//   (`manifest`) plus its Ed25519 signature, so the client never has to
//   re-serialize JSON to verify it (no canonicalization drift).
// - Publishing an OLDER version than the newest row is refused: even a
//   leaked key must not downgrade the whole fleet.

import { bearerPayload } from "../lib/jwt.js";
import { fail, json } from "../lib/respond.js";
import { sha256Hex } from "../lib/hash.js";
import { touchAndCheck } from "../lib/activity.js";

const VERSION_RE = /^\d+\.\d+\.\d+$/;
const SHA256_RE = /^[0-9a-f]{64}$/;

function semver(v) {
  const m = String(v || "").match(/^(\d+)\.(\d+)\.(\d+)$/);
  return m ? [+m[1], +m[2], +m[3]] : null;
}

function cmpSemver(a, b) {
  const A = semver(a);
  const B = semver(b);
  if (!A || !B) return 0;
  for (let i = 0; i < 3; i += 1) {
    if (A[i] !== B[i]) return A[i] < B[i] ? -1 : 1;
  }
  return 0;
}

function timingSafeEqHex(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function publishKeyOk(request, env) {
  const given = request.headers.get("x-pulse-release-key") || "";
  if (!env.RELEASE_KEY || !given) return false;
  const givenHash = await sha256Hex("k:" + given);
  const wantHash = await sha256Hex("k:" + env.RELEASE_KEY);
  return timingSafeEqHex(givenHash, wantHash);
}

// GET /updates/latest?version=<client semver>  (Bearer; registered + active)
export async function latestRelease(request, env) {
  const p = await bearerPayload(env, request);
  if (!p || !p.sub) return fail(401, "invalid or expired token");
  const uid = String(p.sub);
  const window = Number(env.UPDATE_ACTIVITY_DAYS || 90);
  const active = await touchAndCheck(env, uid, window);
  if (!active) return fail(403, "account inactive — sign in again to get updates");

  const url = new URL(request.url);
  const clientVersion = String(url.searchParams.get("version") || "").slice(0, 16);
  if (clientVersion && !semver(clientVersion)) {
    return fail(400, "version must be X.Y.Z");
  }

  const rows = (await env.DB.prepare(`SELECT * FROM releases`).all()).results || [];
  let latest = null;
  for (const r of rows) {
    if (!latest || cmpSemver(r.version, latest.version) > 0) latest = r;
  }
  if (!latest) return json({ latest: null, update_available: false });

  return json({
    latest: latest.version,
    channel: latest.channel || "stable",
    notes: latest.notes || "",
    html_url: latest.html_url || "",
    // the app verifies EXACTLY this string against manifest_sig
    manifest: latest.manifest || "",
    manifest_sig: latest.manifest_sig || "",
    manifest_sig_alg: "ed25519",
    sha256: latest.sha256 || "",
    published_at: latest.published_at || "",
    mandatory: !!Number(latest.mandatory),
    min_supported: latest.min_supported || "",
    // front-desk delivery when R2 is provisioned: same base URL, same
    // auth, streamed from the private bucket. Without R2 (free-tier roll-
    // out), this stays "" and the client uses the GitHub fallback_url;
    // switching to R2 later is a bucket + binding change, no app release.
    download_url: env.BUCKET && latest.asset_name ? `/dl/${latest.asset_name}` : "",
    // resilience: if R2 ever loses the object the client can still fetch
    // the identical, signed file from the GitHub release
    fallback_url: env.GITHUB_REPO
      ? `https://github.com/${env.GITHUB_REPO}/releases/download/v${latest.version}/${latest.asset_name}`
      : "",
    update_available: cmpSemver(latest.version, clientVersion) > 0,
  });
}

// POST /updates/publish  (x-pulse-release-key; called by CI on release)
export async function publishRelease(request, env) {
  if (!env.RELEASE_KEY) return fail(503, "publishing not configured");
  if (!(await publishKeyOk(request, env))) return fail(401, "bad release key");

  const body = await request.json().catch(() => null);
  if (!body || typeof body !== "object") return fail(400, "json body required");

  const version = String(body.version || "");
  const sha256 = String(body.sha256 || "").toLowerCase();
  const publishedAt = String(body.published_at || "");
  const manifest = String(body.manifest || "");
  const manifestSig = String(body.manifest_sig || "");  const minSupported = String(body.min_supported || "");
  const mandatory = body.mandatory ? 1 : 0;
  const notes = String(body.notes || "").slice(0, 20000);
  const htmlUrl = String(body.html_url || "");

  if (!VERSION_RE.test(version)) return fail(400, "version must be X.Y.Z");
  if (!SHA256_RE.test(sha256)) return fail(400, "sha256 must be 64 hex chars");
  if (!publishedAt) return fail(400, "published_at required");
  const assetName = `PulseHWM-Setup-${version}.exe`;
  if (htmlUrl && !/^https:\/\//.test(htmlUrl)) {
    return fail(400, "html_url must be https");
  }

  // the manifest is what the app will verify with the embedded Ed25519
  // public key — it must agree with the standalone fields AND name EXACTLY
  // the asset that corresponds to this version (no mismatched pairs)
  // REQUIRED: the desktop client hard-refuses any release without a valid
  // manifest + signature, so publishing one would blind every check
  if (!manifest || !manifestSig) {
    return fail(400, "manifest and manifest_sig are required");
  }
  {
    let m = null;
    try {
      m = JSON.parse(manifest);
    } catch {
      return fail(400, "manifest must be JSON");
    }
    if (
      !m ||
      semver(m.version)?.join(".") !== semver(version)?.join(".") ||
      m.asset_name !== assetName ||
      String(m.sha256 || "").toLowerCase() !== sha256
    ) {
      return fail(400, "manifest fields disagree with the request body");
    }
  }

  const existing = (await env.DB.prepare(`SELECT * FROM releases`).all()).results || [];
  let maxExisting = null;
  for (const r of existing) {
    if (!maxExisting || cmpSemver(r.version, maxExisting.version) > 0) maxExisting = r;
  }
  // reject only STRICTLY older versions: an idempotent re-run of CI for
  // the newest version must reach the ON CONFLICT upsert below (same
  // version = metadata refresh), matching scripts/publish_release.py docs
  if (maxExisting && cmpSemver(version, maxExisting.version) < 0) {
    return fail(
      409,
      `refusing an update that is not newer than ${maxExisting.version} (anti-downgrade)`
    );
  }

  await env.DB.prepare(
    `INSERT INTO releases
       (version, channel, notes, html_url, asset_name, sha256, manifest,
        manifest_sig, published_at, min_supported, mandatory, created_at)
     VALUES (?, 'stable', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT (version) DO UPDATE SET
       notes = excluded.notes,
       html_url = excluded.html_url,
       asset_name = excluded.asset_name,
       sha256 = excluded.sha256,
       manifest = excluded.manifest,
       manifest_sig = excluded.manifest_sig,
       published_at = excluded.published_at,
       min_supported = excluded.min_supported,
       mandatory = excluded.mandatory`
  )
    .bind(
      version,
      notes,
      htmlUrl,
      assetName,
      sha256,
      manifest,
      manifestSig,
      publishedAt,
      minSupported,
      mandatory,
      new Date().toISOString().replace("Z", "+00:00")
    )
    .run();

  return json({ ok: true, version, asset_name: assetName });
}
