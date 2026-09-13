// PulseHWM Cloud Worker — Cloudflare Workers + D1.
//
// Replaces Supabase Auth + PostgREST with one Worker that speaks the
// SAME JSON shapes and URL paths the desktop client already uses, so
// pulse_hwm/auth/rest.py only swaps its base URL:
//
//   POST /auth/v1/signup    {email,password,code_challenge?} → tokens or {user}
//   POST /auth/v1/token?grant_type=password|refresh_token|pkce
//   POST /auth/v1/recover   {email,code_challenge} → reset email
//   GET  /auth/v1/authorize?provider=google|github&code_challenge=...&state
//   GET  /cb/google|github?state&code → 302 pulsehwm://auth-callback?code=
//   POST /auth/v1/logout    (Bearer; no server-side revocation, see note)
//   GET  /auth/v1/user      (Bearer) → {id,email}
//   GET  /auth/confirm?code=...&redirect_to=... → 302 pulsehwm://...?code=
//   GET|POST /rest/v1/user_settings|user_sites (Bearer; rows fenced to JWT user)
//
// Security model (the RLS stand-in): provider secrets / JWT key / Brevo
// key live ONLY in Worker env (`wrangler secret put`). /rest/v1 rows are
// always stamped + filtered by the JWT subject. Password hashing is
// PBKDF2-SHA256 20k + pepper (Workers free CPU is 10 ms; the long secret
// pepper is what protects an offline dump).

const CODE_TTL_S = 24 * 3600; // email links / app codes

const enc = new TextEncoder();

// ── utilities ──────────────────────────────────────────────────────────

function nowIso() {
  return new Date().toISOString().replace("Z", "+00:00");
}

function isoIn(seconds) {
  return new Date(Date.now() + seconds * 1000).toISOString().replace("Z", "+00:00");
}

function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlJson(obj) {
  return b64url(enc.encode(JSON.stringify(obj)));
}

function fromB64url(text) {
  const pad = text.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(
    atob(pad + "=".repeat((4 - (pad.length % 4)) % 4)),
    (c) => c.charCodeAt(0)
  );
}

function randToken(nBytes = 32) {
  const b = new Uint8Array(nBytes);
  crypto.getRandomValues(b);
  return b64url(b);
}

function randHex(nBytes = 16) {
  const b = new Uint8Array(nBytes);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(text) {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(text));
  return [...new Uint8Array(d)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// PKCE challenge = base64url(sha256(verifier)) — must equal pkce.py
async function pkceChallenge(verifier) {
  return b64url(await crypto.subtle.digest("SHA-256", enc.encode(verifier)));
}

async function hmacJwtSign(secret, payload) {
  const head = b64urlJson({ alg: "HS256", typ: "JWT" });
  const body = b64urlJson(payload);
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(`${head}.${body}`));
  return `${head}.${body}.${b64url(sig)}`;
}

async function hmacJwtVerify(secret, token) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
  );
  let ok;
  try {
    ok = await crypto.subtle.verify(
      "HMAC",
      key,
      fromB64url(parts[2]),
      enc.encode(`${parts[0]}.${parts[1]}`)
    );
  } catch {
    return null;
  }
  if (!ok) return null;
  try {
    return JSON.parse(new TextDecoder().decode(fromB64url(parts[1])));
  } catch {
    return null;
  }
}

const ITER = 20000;

async function pbkdf2Hex(text, saltText, iterations = ITER) {
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(text),
    "PBKDF2",
    false,
    ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: enc.encode(saltText), iterations },
    key,
    256
  );
  return [...new Uint8Array(bits)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function hashPassword(password, saltHex, pepper) {
  return pbkdf2Hex(password, saltHex + "|" + pepper);
}

// ── email (Brevo HTTP API) ─────────────────────────────────────────────

async function sendMail(env, to, msg) {
  if (!env.BREVO_API_KEY || !env.SENDER_EMAIL) return false;
  const r = await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: {
      "api-key": env.BREVO_API_KEY,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({
      sender: { email: env.SENDER_EMAIL, name: env.SENDER_NAME || "Pulse-HWM" },
      to: [{ email: to }],
      subject: msg.subject,
      textContent: msg.text,
      htmlContent: msg.html,
    }),
  });
  return r.ok;
}

function linkMail(url) {
  const safe = url.replace(/&/g, "&amp;");
  return {
    subject: "Pulse-HWM sign-in link",
    text: `One-time sign-in link (valid 24h):\n${url}\n\nIf you did not request this, ignore the email.`,
    html: `<p style="font-family:monospace;color:#333">PULSE-HWM</p>
<p style="font-family:sans-serif">Open this one-time link to finish signing in (valid 24h):<br>
<a href="${safe}">Confirm sign-in</a></p>
<p style="color:#888;font-family:sans-serif">If you did not request this, ignore this email.</p>`,
  };
}

// ── tokens ─────────────────────────────────────────────────────────────

async function mintTokens(env, user) {
  const iat = Math.floor(Date.now() / 1000);
  const ttl = Number(env.ACCESS_TTL_S || 3600);
  const access = await hmacJwtSign(env.JWT_SECRET, {
    sub: user.id,
    email: user.email,
    iat,
    exp: iat + ttl,
  });
  const refresh = randToken();
  await env.DB.prepare(
    `INSERT INTO refresh_tokens (token_hash, user_id, family_id, expires_at, revoked, created_at)
     VALUES (?, ?, ?, ?, 0, ?)`
  )
    .bind(
      await sha256Hex(refresh),
      user.id,
      crypto.randomUUID(),
      isoIn(Number(env.REFRESH_TTL_S || 2592000)),
      nowIso()
    )
    .run();
  return { access_token: access, refresh_token: refresh, expires_in: ttl, user };
}

// Replay of an already-rotated refresh token = the family was stolen;
// Supabase-style: kill the whole family so any thief is locked out too.
async function refreshRotate(env, rawToken) {
  const hash = await sha256Hex(rawToken);
  const row = await env.DB.prepare(
    `SELECT rt.*, u.email FROM refresh_tokens rt JOIN users u ON u.id = rt.user_id
     WHERE rt.token_hash = ?`
  )
    .bind(hash)
    .first();
  if (!row || row.expires_at < nowIso()) {
    if (row && row.revoked) {
      // replayed a rotated token: the family may be stolen — kill it all
      await env.DB.prepare(`UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ?`)
        .bind(row.family_id)
        .run();
    }
    return null;
  }
  await env.DB.prepare(`UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?`)
    .bind(hash)
    .run();
  const access = await hmacJwtSign(env.JWT_SECRET, {
    sub: row.user_id,
    email: row.email,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + Number(env.ACCESS_TTL_S || 3600),
  });
  const refresh = randToken();
  await env.DB.prepare(
    `INSERT INTO refresh_tokens (token_hash, user_id, family_id, expires_at, revoked, created_at)
     VALUES (?, ?, ?, ?, 0, ?)`
  )
    .bind(await sha256Hex(refresh), row.user_id, row.family_id, isoIn(Number(env.REFRESH_TTL_S || 2592000)), nowIso())
    .run();
  return {
    tokens: {
      access_token: access,
      refresh_token: refresh,
      expires_in: Number(env.ACCESS_TTL_S || 3600),
    },
    user: { id: row.user_id, email: row.email },
  };
}

async function bearerPayload(env, request) {
  const auth = request.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) return null;
  return hmacJwtVerify(env.JWT_SECRET, auth.slice(7));
}

// ── intent codes (email-link / OAuth app codes) ───────────────────────

async function makeIntent(env, userId, kind, challenge) {
  const code = randToken();
  await env.DB.prepare(
    `INSERT INTO intent_codes (code, user_id, code_challenge, kind, expires_at)
     VALUES (?, ?, ?, ?, ?)`
  )
    .bind(code, userId, challenge, kind, isoIn(CODE_TTL_S))
    .run();
  return code;
}

async function consumeIntent(env, authCode, verifier) {
  const row = await env.DB.prepare(`SELECT * FROM intent_codes WHERE code = ?`)
    .bind(authCode)
    .first();
  if (!row || row.expires_at < nowIso()) return null;
  await env.DB.prepare(`DELETE FROM intent_codes WHERE code = ?`)
    .bind(authCode)
    .run();
  // PKCE: the verifier the parked app holds must hash to the stored challenge
  const challenge = await pkceChallenge(verifier || "");
  if (challenge !== row.code_challenge) return null;
  return row;
}

// ── provider exchanges ─────────────────────────────────────────────────

async function providerProfile(env, provider, code, origin) {
  if (provider === "google") {
    const r = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: env.GOOGLE_CLIENT_ID,
        client_secret: env.GOOGLE_CLIENT_SECRET,
        redirect_uri: `${origin}/cb/google`,
        grant_type: "authorization_code",
      }),
    });
    if (!r.ok) return null;
    const t = await r.json();
    const me = await fetch("https://openidconnect.googleapis.com/v1/userinfo", {
      headers: { Authorization: `Bearer ${t.access_token}` },
    });
    if (!me.ok) return null;
    const p = await me.json();
    return { email: String(p.email || ""), uid: `google:${p.sub}` };
  }
  if (provider === "github") {
    const r = await fetch("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({
        code,
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        redirect_uri: `${origin}/cb/github`,
      }),
    });
    if (!r.ok) return null;
    const t = await r.json();
    if (!t.access_token) return null;
    const me = await fetch("https://api.github.com/user", {
      headers: { Authorization: `Bearer ${t.access_token}`, accept: "application/vnd.github+json" },
    });
    if (!me.ok) return null;
    const p = await me.json();
    let email = p.email;
    if (!email) {
      const em = await fetch("https://api.github.com/user/emails", {
        headers: { Authorization: `Bearer ${t.access_token}`, accept: "application/vnd.github+json" },
      });
      if (em.ok) {
        const list = await em.json();
        const primary = (list || []).find((e) => e.primary) || (list || [])[0];
        email = primary && primary.email;
      }
    }
    return { email: String(email || ""), uid: `github:${p.id}` };
  }
  return null;
}

// ── the router ────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    try {
      return await route(request, env);
    } catch (err) {
      return fail(500, `worker error: ${String(err && err.message).slice(0, 120)}`);
    }
  },
};

async function route(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const method = request.method;

  if (path === "/") return json({ app: "PulseHWM cloud", ok: true });

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
    // client-side sign-out only; other devices may keep their sessions
    return json({});
  }
  if (path === "/auth/v1/user" && method === "GET") {
    const p = await bearerPayload(env, request);
    if (!p) return fail(401, "invalid credentials");
    return json({ sub: p.sub, email: p.email });
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
    const redirect = url.searchParams.get("redirect_to") || "pulsehwm://auth-callback";
    const sep = redirect.includes("?") ? "&" : "?";
    return Response.redirect(`${redirect}${sep}code=${code}`, 302);
  }

  // ── PostgREST-shaped storage (ownership fenced to the JWT user) ────
  if (path.startsWith("/rest/v1/")) {
    return rest(request, env, path.slice("/rest/v1/".length));
  }

  return fail(404, "no such endpoint");
}

// ── signup ─────────────────────────────────────────────────────────────

async function authSignup(request, env) {
  const body = await request.json().catch(() => ({}));
  const email = String(body.email || "").toLowerCase();
  const password = String(body.password || "");
  const challenge = String(body.code_challenge || "");
  if (!email.includes("@")) return fail(400, "unable to validate email address");
  if (password.length < 8) return fail(400, "password must be at least 8 characters");

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
    const salt = randHex();
    const hash = await hashPassword(password, salt, env.HASH_PEPPER || env.JWT_SECRET);
    const id = "u" + randHex(12);
    await env.DB.prepare(
      `INSERT INTO users (id, email, password_hash, provider, email_verified, created_at)
       VALUES (?, ?, ?, 'password', 0, ?)`
    )
      .bind(id, email, `${salt}$${hash}`, nowIso())
      .run();
    user = { id, email };
  } else {
    // unconfirmed signup retried: update password, re-use the same user
    const salt = randHex();
    const hash = await hashPassword(password, salt, env.HASH_PEPPER || env.JWT_SECRET);
    await env.DB.prepare(`UPDATE users SET password_hash = ? WHERE id = ?`)
      .bind(`${salt}$${hash}`, user.id)
      .run();
  }

  if (!requireEmail) {
    const tokens = await mintTokens(env, user);
    return json({ ...tokens, user });
  }

  const code = await makeIntent(env, user.id, "signup-verify", challenge);
  const redirectTo = String(body.redirect_to || "pulsehwm://auth-callback");
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
    link.search = `?code=${code}&redirect_to=${encodeURIComponent("pulsehwm://auth-callback")}`;
    await sendMail(env, email, linkMail(link.toString()));
  }
  return json({}); // shape parity: no enumeration, no tokens
}

// ── OAuth provider authorize/callback ─────────────────────────────────

async function oauthAuthorize(env, url, origin) {
  const provider = url.searchParams.get("provider") || "";
  const challenge = url.searchParams.get("code_challenge") || "";
  if (!provider || !challenge) return fail(400, "provider and code_challenge required");
  const state = randToken();
  await env.DB.prepare(
    `INSERT INTO oauth_states (state, provider, code_challenge, app_code, expires_at)
     VALUES (?, ?, ?, '', ?)`
  )
    .bind(state, provider, challenge, isoIn(900))
    .run();
  let authorizeUrl;
  if (provider === "google") {
    authorizeUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    authorizeUrl.search = new URLSearchParams({
      client_id: env.GOOGLE_CLIENT_ID || "",
      redirect_uri: `${origin}/cb/google`,
      response_type: "code",
      scope: "openid email profile",
      state,
      prompt: "select_account",
    });
  } else if (provider === "github") {
    authorizeUrl = new URL("https://github.com/login/oauth/authorize");
    authorizeUrl.search = new URLSearchParams({
      client_id: env.GITHUB_CLIENT_ID || "",
      redirect_uri: `${origin}/cb/github`,
      scope: "read:user user:email",
      state,
    });
  } else {
    return fail(400, "unsupported provider");
  }
  return Response.redirect(authorizeUrl.toString(), 302);
}

async function oauthCallback(env, url, provider) {
  const state = url.searchParams.get("state") || "";
  const code = url.searchParams.get("code") || "";
  const errDesc = url.searchParams.get("error_description") || url.searchParams.get("error") || "";
  const back = "pulsehwm://auth-callback";
  const seep = errDesc ? "?error_description=" : "?code=";
  if (errDesc) {
    return Response.redirect(`${back}${seep}${encodeURIComponent(errDesc)}`, 302);
  }
  const row = await env.DB.prepare(`SELECT * FROM oauth_states WHERE state = ?`)
    .bind(state)
    .first();
  if (row && row.provider === provider) {
    await env.DB.prepare(`DELETE FROM oauth_states WHERE state = ?`).bind(state).run();
  }
  if (!code || !row || row.provider !== provider) {
    return fail(400, "unknown or expired sign-in state");
  }
  const profile = await providerProfile(env, provider, code, url.origin);
  if (!profile) return Response.redirect(`${back}?error_description=${encodeURIComponent("provider sign-in failed")}`, 302);
  if (!profile.email) return Response.redirect(`${back}?error_description=${encodeURIComponent("provider did not share an email")}`, 302);
  const email = profile.email.toLowerCase();

  let user = await env.DB.prepare(`SELECT id, email FROM users WHERE email = ?`)
    .bind(email)
    .first();
  if (!user) {
    // stable id from the provider identity (re-signing in recreates it)
    const id = "u" + (await sha256Hex(profile.uid)).slice(0, 32);
    user = { id, email };
    await env.DB.prepare(
      `INSERT OR IGNORE INTO users (id, email, password_hash, provider, email_verified, created_at)
       VALUES (?, ?, NULL, ?, 1, ?)`
    )
      .bind(id, email, provider, nowIso())
      .run();
  }
  // the app code is PKCE-bound like any other intent code
  const appCode = await makeIntent(env, user.id, `oauth-${provider}`, row.code_challenge);
  return Response.redirect(`${back}?code=${encodeURIComponent(appCode)}`, 302);
}

// ── data (PostgREST shapes; fenced to the JWT user) ───────────────────

const DATA_TABLES = {
  user_settings: {
    pk: "key",
    columns: ["user_id", "key", "value", "updated_at"],
    cast: {},
  },
  user_sites: {
    pk: "site_uuid",
    columns: [
      "user_id", "site_uuid", "name", "url", "method", "timeout_s",
      "expected_status", "keyword", "enabled", "deleted", "updated_at",
    ],
    cast: { enabled: "int", deleted: "int" },
  },
};

async function rest(request, env, table) {
  const spec = DATA_TABLES[table];
  if (!spec) return fail(404, "unknown table");
  const payload = await bearerPayload(env, request);
  if (!payload || !payload.sub) return fail(401, "invalid or expired token");
  const uid = String(payload.sub);

  if (request.method === "GET") {
    const url = new URL(request.url);
    const wanted = (url.searchParams.get("user_id") || "").replace(/^eq\./, "");
    if (wanted !== uid) return fail(403, "you may only read your own rows");
    const order = url.searchParams.get("order");
    const limit = Number((url.searchParams.get("limit") || "100").replace(/^limit\./, "")) || 100;
    let sql = `SELECT * FROM ${table} WHERE user_id = ?`;
    if (order) {
      const [col, dir] = order.split(".");
      if (spec.columns.includes(col)) sql += ` ORDER BY ${col} ${dir === "desc" ? "DESC" : "ASC"}`;
    }
    sql += ` LIMIT ${Math.min(Math.max(limit, 1), 500)}`;
    const rows = await env.DB.prepare(sql).bind(uid).all();
    const out = (rows.results || []).map((r) => ({
      ...r,
      enabled: spec.cast.enabled ? Number(r.enabled) : r.enabled,
      deleted: spec.cast.deleted ? Number(r.deleted) : r.deleted,
      timeout_s: Number(r.timeout_s),
      expected_status: Number(r.expected_status),
    }));
    return json(out);
  }

  if (request.method === "POST") {
    const bodyRows = await request.json().catch(() => []);
    if (!Array.isArray(bodyRows)) return fail(400, "body must be a row array");
    let okRows = 0;
    for (const raw of bodyRows) {
      if (!raw || typeof raw !== "object") continue;
      const pkVal = raw[spec.pk];
      if (pkVal === undefined || pkVal === null || String(pkVal) === "") continue;
      const cols = spec.columns.filter((c) => c !== spec.pk);
      const vals = [];
      for (const c of cols) {
        let v = raw[c];
        if (spec.cast[c] === "int") v = v ? 1 : 0;
        vals.push(v === undefined ? null : v);
      }
      // colList includes user_id FIRST (it is also the conflict half)
      const colList = ["user_id", ...cols, spec.pk].join(", ");
      const placeholders = ["user_id"].concat(cols, [spec.pk]).map(() => "?");
      const updates = cols.map((c) => `${c} = excluded.${c}`).join(", ");
      await env.DB.prepare(
        `INSERT INTO ${table} (${colList}) VALUES (${placeholders.join(", ")})
         ON CONFLICT (user_id, ${spec.pk}) DO UPDATE SET ${updates}`
      )
        .bind(uid, ...vals, raw[spec.pk])
        .run();
      okRows += 1;
    }
    return json({ rows: okRows });
  }

  return fail(405, "method not allowed");
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fail(status, msg) {
  return json({ msg, error_description: msg }, status);
}
