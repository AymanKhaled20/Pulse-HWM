// Password signup/grants/recover + logout + user (moved verbatim).

import { fail, json, nowIso } from "../lib/respond.js";
import { randHex } from "../lib/encoding.js";
import { bearerPayload } from "../lib/jwt.js";
import { constantTimeEqual, hashPassword } from "../lib/hash.js";
import { mintTokens, refreshRotate } from "../lib/tokens.js";
import { makeIntent, consumeIntent } from "../lib/ints.js";
import { sendMail, linkMail } from "../lib/mail.js";
import { touchAndCheck } from "../lib/activity.js";

// ── signup ─────────────────────────────────────────────────────────────

// shown when a signup arrives without the app's 16+ confirmation
export const AGE_REQUIRED_MSG =
  "you must be 16 or older to create an account (update Pulse-HWM if you don't see a date of birth field)";

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
  // Age gate (GDPR Art. 8 / COPPA): the app asks for a date of birth and
  // only sends this flag for users aged 16+. Older app versions don't
  // send it, so they can't create accounts until they update.
  if (body.age_confirmed !== true) {
    return fail(400, AGE_REQUIRED_MSG);
  }

  // when confirmation email is required, a parked signup MUST come with
  // the PKCE challenge the parked verifier claims — else the confirm
  // flow can never succeed and the account stays unconfirmed forever
  if (requireEmail && !challenge) {
    return fail(400, "code_challenge required");
  }
  const salt = randHex();
  const hash = await hashPassword(password, salt, env.HASH_PEPPER || env.JWT_SECRET);
  const storedHash = `${salt}$${hash}`;

  let user = existing;
  if (!user) {
    const id = "u" + randHex(12);
    // no confirm-email mode (no Brevo) also verifies immediately — else the
    // account starts usable but the password grant would reject it later
    // as "email not confirmed" once the first session expires
    const verified = requireEmail ? 0 : 1;
    // In email mode the row starts WITHOUT a password: the hash rides on
    // the emailed code (makeIntent below) and only lands on the account
    // when the mailbox owner redeems it. Storing it here let anyone who
    // signed up first with someone else's email keep a working password
    // on that person's account once they confirmed it (pre-hijack).
    // age_confirmed_at records WHEN the 16+ confirmation was given (the
    // birth date itself is never sent or stored — data minimisation)
    await env.DB.prepare(
      `INSERT INTO users (id, email, password_hash, provider, email_verified, created_at, age_confirmed_at)
       VALUES (?, ?, ?, 'password', ${verified}, ?, ?)`
    )
      .bind(id, email, requireEmail ? null : storedHash, nowIso(), nowIso())
      .run();
    user = { id, email };
  }
  // unconfirmed signup retried: re-use the same user row; this attempt's
  // password travels on its own emailed code like the first one did

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

  const code = await makeIntent(env, user.id, "signup-verify", challenge, storedHash);
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
    if (!user) return fail(400, "invalid login credentials");
    const password = String(body.password || "");
    // same input bound as signup: many chars only adds useless PBKDF2 work
    if (password.length > 200) return fail(400, "password is too long");
    // checked before password_hash: an unconfirmed signup has no password
    // on the row yet (it waits on the emailed code)
    if (!Number(user.email_verified)) {
      return fail(400, "email not confirmed yet — check your inbox");
    }
    if (!user.password_hash) {
      return fail(400, "this account uses Google/GitHub sign-in");
    }
    const [salt, stored] = String(user.password_hash).split("$");
    const attempt = await hashPassword(password, salt, env.HASH_PEPPER || env.JWT_SECRET);
    if (!constantTimeEqual(attempt, stored)) return fail(400, "invalid login credentials");
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
      `SELECT id, email, email_verified FROM users WHERE id = ?`
    )
      .bind(row.user_id)
      .first();
    if (!user) return fail(400, "user no longer exists");
    if (!Number(user.email_verified)) {
      // first proof of mailbox ownership. A signup code carries the
      // password its owner chose; any other code (reset link, Google/
      // GitHub) clears whatever password an unconfirmed row held, since
      // nobody proved they owned the email when it was typed in.
      const newHash = row.kind === "signup-verify" ? row.password_hash || null : null;
      await env.DB.prepare(
        `UPDATE users SET email_verified = 1, password_hash = ?
         WHERE id = ? AND email_verified = 0`
      )
        .bind(newHash, user.id)
        .run();
    }
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