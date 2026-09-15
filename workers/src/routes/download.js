// Front-desk asset delivery: the ONLY way the app downloads an update
// binary. Requires the same registerd-user JWT + activity window as the
// metadata endpoint (updates are members-only end to end), and streams the
// R2 object straight through — no buffering, Range passthrough included so
// a dropped 63 MB download can resume.

import { bearerPayload } from "../lib/jwt.js";
import { fail } from "../lib/respond.js";
import { touchAndCheck } from "../lib/activity.js";

const ASSET_RE = /^PulseHWM-Setup-\d+\.\d+\.\d+\.exe$/;

export async function downloadAsset(request, env, assetName) {
  const p = await bearerPayload(env, request);
  if (!p || !p.sub) return fail(401, "invalid or expired token");
  if (!ASSET_RE.test(assetName)) return fail(404, "unknown asset");
  const active = await touchAndCheck(
    env,
    String(p.sub),
    Number(env.UPDATE_ACTIVITY_DAYS || 90)
  );
  if (!active) return fail(403, "account inactive — sign in again to get updates");

  // serve ONLY binaries that a published release row vouches for
  const row = await env.DB.prepare(`SELECT * FROM releases WHERE asset_name = ?`)
    .bind(assetName)
    .first();
  if (!row) return fail(404, "unknown asset");
  if (!env.BUCKET) return fail(503, "download storage not configured");

  const range = request.headers.get("range");
  const obj = await env.BUCKET.get(assetName, range ? { range: request.headers } : undefined);
  if (!obj) return fail(404, "asset missing from storage");

  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  headers.set("content-disposition", `attachment; filename="${assetName}"`);
  // never a shared/proxied cache: this route is per-account
  headers.set("cache-control", "private, no-store");

  if (range && obj.range && typeof obj.size === "number") {
    headers.set(
      "content-range",
      `bytes ${obj.range.offset}-${obj.range.offset + obj.range.length - 1}/${obj.size}`
    );
    return new Response(obj.body, { status: 206, headers });
  }
  return new Response(obj.body, { status: 200, headers });
}
