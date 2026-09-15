// Password signup/grants/recover + logout + user (moved verbatim).

import { fail, json, nowIso } from "../lib/respond.js";
import { randHex } from "../lib/encoding.js";
import { bearerPayload } from "../lib/jwt.js";
import { hashPassword } from "../lib/hash.js";
import { mintTokens, refreshRotate } from "../lib/tokens.js";
import { makeIntent, consumeIntent } from "../lib/ints.js";
import { sendMail, linkMail } from "../lib/mail.js";
import { touchAndCheck } from "../lib/activity.js";

// ── signup ─────────────────────────────────────────────────────────────

// redirect_to MUST be the app deep link: accepting arbitrary URLs would
// turn the emailed one-time links into an open redirect / phishing vector
function safeRedirectTo(v) {
  const s = String(v || "");
  // exact target only: any suffixed lookalike (pulsehwm://auth-callback.evil)
  // is still forwarded by Windows to Pulse but must not be accepted
  return s === "pulsehwm://auth-callback" ? s : "pulsehwm://auth-callback";
}

async function authSignup(request, env) {
  const body = await request.json().catch(() => ({}));
  const url = new URL(request.url);
  const email = String(body.email || "").toLowerCase();
  const password = String(body.password || "");
  const challenge = String(body.code_challenge || "");
  if (!email.includes("@")) return fail(400, "unable to validate email address");
  if (password.length < 8) return fail(400, "password must be at least 8 characters");
  // bound the expensive PBKDF2 work: absurd inputs can't burn Worker CPU
  if (password.length > 200) return fail(400, "password is too long");

  const existing = await env.DB.prepare(`SELECT * FROM users WHERE email = ?`)
    .bind(email)
    .first();
  const confirmed = Boolean(existing && existing.email_verified);

  // email mode OFF (no Brevo configured) → confirm-email is skipped and
  // signup returns tokens immediately (dev/self-host convenience mode)
  const requireEmail = Boolean(env.BREVO_API_KEY && env.SENDER_EMAIL);

  if (confirmed) {
    return fail(400, "user already registered");
  }

  let user = existing;
  if (!user) {
    // when confirmation email is required, a parked signup MUST come with
    // the PKCE challenge the parked verifier claims — else the confirm
    // flow can never succeed and the account stays unconfirmed forever
    if (requireEmail && !challenge) {
      return fail(400, "code_challenge required");
    }
    const salt = randHex();
    const hash = await hashPassword(password, salt, env.HASH_PEPPER || env.JWT_SECRET);
    const id = "u" + randHex(12);
    // no confirm-email mode (no Brevo) also verifies immediately — else the
    // account starts usable but the password grant would reject it later
    // as "email not confirmed" once the first session expires
    const verified = requireEmail ? 0 : 1;
    await env.DB.prepare(
      `INSERT INTO users (id, email, password_hash, provider, email_verified, created_at)
       VALUES (?, ?, ?, 'password', ${verified}, ?)`
    )
      .bind(id, email, `${salt}$${hash}`, nowIso())
      .run();
    user = { id, email };
  }
  // unconfirmed signup retried: re-use the same user row but do NOT
  // overwrite its password hash from a pre-auth request — the stored
  // credentials only come from the first (unverified) signup attempt

  if (!requireEmail) {
    // dev/self-host convenience mode still must not mint tokens for an
    // EXISTING row whose email never got confirmed (e.g. Brevo was enabled
    // earlier) — that row belongs to a signup the owner never completed
    if (existing && !confirmed) {
      return fail(400, "user already registered");
    }
    const tokens = await mintTokens(env, user);
    return json({ ...tokens, user });
  }

  const code = await makeIntent(env, user.id, "signup-verify", challenge);
  // the client sends redirect_to as a QUERY param (rest.py contract) —
  // accept both spellings, always through the allowlist
  const redirectTo = safeRedirectTo(
    url.searchParams.get("redirect_to") || body.redirect_to
  );
  const link = new URL(request.url);
  link.pathname = "/auth/confirm";
  link.search = `?code=${code}&redirect_to=${encodeURIComponent(redirectTo)}`;
  const sent = await sendMail(env, email, linkMail(link.toString()));
  if (!sent) return fail(500, "could not send confirmation email");
  return json({ user: { id: user.id, email } }); // NO tokens → client waits for the link
}

// ── token grant endpoint ───────────────────────────────────────────────

async function authToken(request, env, grantType) {
  const body = await request.json().catch(() => ({}));
  if (grantType === "password") {
    const email = String(body.email || "").toLowerCase();
    const user = await env.DB.prepare(`SELECT * FROM users WHERE email = ?`)
      .bind(email)
      .first();
    if (!user || !user.password_hash) {
      return fail(400, user ? "this account uses Google/GitHub sign-in" : "invalid login credentials");
    }
    const password = String(body.password || "");
    // same input bound as signup: many chars only adds useless PBKDF2 work
    if (password.length > 200) return fail(400, "password is too long");
    if (!Number(user.email_verified)) {
      return fail(400, "email not confirmed yet — check your inbox");
    }
    const [salt, stored] = String(user.password_hash).split("$");
    const attempt = await hashPassword(String(body.password || ""), salt, env.HASH_PEPPER || env.JWT_SECRET);
    if (attempt !== stored) return fail(400, "invalid login credentials");
    const tokens = await mintTokens(env, user);
    return json({ ...tokens, user: { id: user.id, email: user.email } });
  }
  if (grantType === "refresh_token") {
    const rotated = await refreshRotate(env, String(body.refresh_token || ""));
    if (!rotated) return fail(400, "invalid refresh token");
    return json({
      ...rotated.tokens,
      user: { id: rotated.user.id, email: rotated.user.email },
    });
  }
  if (grantType === "pkce") {
    const row = await consumeIntent(
      env,
      String(body.auth_code || ""),
      String(body.code_verifier || "")
    );
    if (!row) return fail(400, "invalid auth code or verifier");
    const user = await env.DB.prepare(
      `SELECT id, email FROM users WHERE id = ?`
    )
      .bind(row.user_id)
      .first();
    if (!user) return fail(400, "user no longer exists");
    await env.DB.prepare(`UPDATE users SET email_verified = 1 WHERE id = ?`)
      .bind(user.id)
      .run();
    const tokens = await mintTokens(env, user);
    return json({ ...tokens, user: { id: user.id, email: user.email } });
  }
  return fail(400, "unsupported grant type");
}

// ── password reset email ───────────────────────────────────────────────

async function authRecover(request, env) {
  const body = await request.json().catch(() => ({}));
  const email = String(body.email || "").toLowerCase();
  const challenge = String(body.code_challenge || "");
  const user = await env.DB.prepare(`SELECT id FROM users WHERE email = ?`)
    .bind(email)
    .first();
  if (user && challenge) {
    const code = await makeIntent(env, user.id, "recover", challenge);
    const link = new URL(request.url);
    link.pathname = "/auth/confirm";
    // honour the client's redirect contract (query param), allowlisted
    const redirectTo = safeRedirectTo(link.searchParams.get("redirect_to"));
    link.search = `?code=${code}&redirect_to=${encodeURIComponent(redirectTo)}`;
    await sendMail(env, email, linkMail(link.toString()));
  }
  return json({}); // shape parity: no enumeration, no tokens
}

export { authSignup, authToken, authRecover, handleLogout, handleUser };

// moved verbatim from route(): server-side sign-out revokes every active
// refresh token for the bearer user (all sessions on all devices)
async function handleLogout(env, request) {
  const p = await bearerPayload(env, request);
  if (!p || !p.sub) return fail(401, "invalid credentials");
  await env.DB.prepare(
    `UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0`
  )
    .bind(p.sub)
    .run();
  return json({});
}

// moved verbatim from route(): the client reads `id` (session._adopt), keep email as-is
async function handleUser(env, request) {
  const p = await bearerPayload(env, request);
  if (!p) return fail(401, "invalid credentials");
  // session resume is the strongest activity signal — track it (throttled)
  await touchAndCheck(env, String(p.sub), 0);
  return json({ id: p.sub, email: p.email });
}