// PulseHWM Cloud Worker — Cloudflare Workers + D1.
//
// Replaces Supabase Auth + PostgREST with one Worker that speaks the
// SAME JSON shapes and URL paths the desktop client already uses, so
// pulse_hwm/cloud/rest.py only swaps its base URL:
//
//   POST /auth/v1/signup    {email,password,code_challenge?} → tokens or {user}
//   POST /auth/v1/token?grant_type=password|refresh_token|pkce
//   POST /auth/v1/recover   {email,code_challenge} → reset email
//   POST /auth/v1/logout    (Bearer) — revokes all refresh tokens for the user
//   GET  /auth/v1/user      (Bearer) → {id,email}
//   GET  /auth/v1/authorize?provider=google|github&code_challenge=...&state
//   GET  /cb/google|github?state&code → 302 pulsehwm://auth-callback?code=
//   GET  /auth/confirm?code=...&redirect_to=... → 302 pulsehwm://...?code=
//   GET|POST /rest/v1/user_settings|user_sites (Bearer; rows fenced to JWT user)
//
//   Updates (v1.2.0):
//   GET  /updates/latest?version=X.Y.Z  (Bearer; registered + ACTIVE account)
//        → newest release {latest, notes, manifest, manifest_sig, sha256, ...}
//   POST /updates/publish               (x-pulse-release-key; called by CI)
//   GET  /dl/PulseHWM-Setup-X.Y.Z.exe   (Bearer; streams the R2 object)
//
// Security model (the RLS stand-in): provider secrets / JWT key / Brevo
// key / release key live ONLY in Worker env (`wrangler secret put`).
// /rest/v1 rows are always stamped + filtered by the JWT subject,
// update endpoints are members-only, and the download route is per-user.
// Password hashing is PBKDF2-SHA256 20k + pepper (Workers free CPU is
// 10 ms; the long secret pepper is what protects an offline dump).

import { fail, json } from "./lib/respond.js";
import { EMAIL_LOGO_PNG_B64 } from "./lib/mail.js";
import { handoffHtml } from "./lib/html.js";
import {
  authSignup,
  authToken,
  authRecover,
  handleLogout,
  handleUser,
} from "./routes/auth.js";
import { oauthAuthorize, oauthCallback } from "./routes/oauth.js";
import { rest } from "./routes/rest.js";
import { latestRelease, publishRelease } from "./routes/updates.js";
import { downloadAsset } from "./routes/download.js";

export async function route(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const method = request.method;

  if (path === "/") return json({ app: "PulseHWM cloud", ok: true });

  // branded icon emailed in sign-in / reset links (see EMAIL_LOGO_PNG_B64)
  if (path === "/branding/logo.png" && method === "GET") {
    return new Response(atob(EMAIL_LOGO_PNG_B64), {
      headers: {
        "content-type": "image/png",
        "cache-control": "public, max-age=86400",
      },
    });
  }

  // ── auth ────────────────────────────────────────────────────────────
  if (path === "/auth/v1/signup" && method === "POST") {
    return authSignup(request, env);
  }
  if (path === "/auth/v1/token" && method === "POST") {
    return authToken(request, env, url.searchParams.get("grant_type") || "");
  }
  if (path === "/auth/v1/recover" && method === "POST") {
    return authRecover(request, env);
  }
  if (path === "/auth/v1/logout" && method === "POST") {
    return handleLogout(env, request);
  }
  if (path === "/auth/v1/user" && method === "GET") {
    return handleUser(env, request);
  }
  if (path === "/auth/v1/authorize" && method === "GET") {
    return oauthAuthorize(env, url, url.origin);
  }

  // ── provider callback + email confirm link ─────────────────────────
  if (path.startsWith("/cb/") && method === "GET") {
    return oauthCallback(env, url, path.slice(4));
  }
  if (path === "/auth/confirm" && method === "GET") {
    const code = url.searchParams.get("code") || "";
    // allowlist: only the app callback may be a confirm target — anything
    // else would make the emailed links an open redirect to any https URL
    const redirect = safeRedirectTo(url.searchParams.get("redirect_to"));
    const sep = redirect.includes("?") ? "&" : "?";
    return handoffHtml(`${redirect}${sep}code=${code}`);
  }

  // ── updates (registered + active accounts only) ────────────────────
  if (path === "/updates/latest" && method === "GET") {
    return latestRelease(request, env);
  }
  if (path === "/updates/publish" && method === "POST") {
    return publishRelease(request, env);
  }
  if (path.startsWith("/dl/") && method === "GET") {
    return downloadAsset(request, env, path.slice(4));
  }

  // ── PostgREST-shaped storage (ownership fenced to the JWT user) ────
  if (path.startsWith("/rest/v1/")) {
    return rest(request, env, path.slice("/rest/v1/".length));
  }

  return fail(404, "no such endpoint");
}

// redirect_to MUST be the app deep link: accepting arbitrary URLs would
// turn the emailed one-time links into an open redirect / phishing vector
function safeRedirectTo(v) {
  const s = String(v || "");
  return s.startsWith("pulsehwm://auth-callback") ? s : "pulsehwm://auth-callback";
}
