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
    const payload = JSON.parse(new TextDecoder().decode(fromB64url(parts[1])));
    // expired access tokens must never authenticate anything, even though
    // the signature is still valid
    if (!payload || typeof payload.exp !== "number" || payload.exp * 1000 <= Date.now()) {
      return null;
    }
    return payload;
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

// the branded icon served from /branding/logo.png (16-color quantized,
// 160px ≈ 4 KB) — embedded as base64 so the worker needs no external asset
const EMAIL_LOGO_PNG_B64 =
 "iVBORw0KGgoAAAANSUhEUgAAAKAAAACgCAYAAACLz2ctAAAMBUlEQVR42u1dv28cxxV+3FspOh4tyqZpJL4QAkUQCWTYECTHUAobLtMkKpImjZsghd2kNfIX+C9wE6RNmjRB3LkRnCpMlAgGHAQwJCOg6UhmCEvUHe8k8vZcnPY0nJsfb3Znf81+H0Dc8e52d3bm2/fevPfmzRIRTckBW5sXKOpemv/fXV6h0dGA2oju8sqp/0dHA6/9IZ/LdG65LWKbONeQX7Pc///v79FXu3ec7nHJlYCvvf4WERFFUUREREmSOHesfGwURQvnSX9jQpIkymO5x8vnMrVXvJb4mue8tn7h/N61PXK/icfkGUsiouHgkD7/9z+djo/zXDAr5BtV3bjYOSpyyuTTvee22fRQ6AZX99BwySC30/RAcogkHs+9rtxPWUiYB5FPIrmQN32vek3/bOdQDZ74uywPi4qIpntQtUd3jHhvPh5kzgOmGyNuf3OumYe0cVnkUxFG9SSapAdX1eikXxaVY7qeTnLr2mp6SEwPk8s42NSyD4mnk6ClEtAHdGpWZ9upOpSjbnUk0kkv1fVd7MwkSRYGKI/NnOUhN5k1WUkoS38f9xL5lH5Z1Io4WJxrqAZUVCO2TlGR2iS1fKp3k+oySf086jq9Px8SSzyHrwcp8mmDFPl0ywPHIUpWSWTrYNX3HDLpjjNdzzSxyGtHZiGS7zGOfUg/n40ySTHbINjIJX9vsyddHizbeUwzdK7U5pgFPvq5TK9InEfaFTFtN0kCnb/Q5g80uXR05+MMMMd3aWpz3glHmR6MLJO0QlWwTVL5djXo1JFOmok2YfpqczbrfGkuDl7xeJ2ryYfksElc1Rio2mGzOWV3jW8yLxHRdPvyVdKFWeSwjBh+UZGwTWG5ZHSXHgyOtd8f7A+Du+fty1e1YT/Xh+r2zk2KX97Ymp9QdQLdxVRS5+v/7TrHAoHmPXTRyhWtOcP1s84lq6/wGtAiEjJm7WxTohsfKF0CPn09QDgYnax5de9Eo5M1L85JoJ3IO9nMnQ0jsv3x+B5GpGXk8xIJ8RlCA2ALZvYDYjIClO2MjrM4W8vM7gDqrXpF8nEjY+LxsY9wCtAePB7fo+FgVft9b+U8K4SZ8i2eTRz6bObe3rmJUWgxDvaHdLCvX/dx5Y23s9mARcRuAaCUfEAAAAEBEBAAKKsbBu4UNX55YynTcTufrtKdLx6gA20EPNgf0sZmBAIqsLbeo9+8e0I/XJ9S79wzIg7Hp/+XMRxP6Q8fH9J776MPa70sswno989SL3py6jMT+TjfA3DDsLG39wSdQMWtXwbjgFIWlRkzogGgqhQtzIKB+vgBYQMClU5Cisp6bfosGCiBgKbsVkhGoPRQnJhwCvsQKGqiEtmqfRL8gOgEKs5VE+nqmgAAIiFUffYvUIENCPULUFXpWKatEaiF2TBEJ2BJmRJQNetts2qGH7DijGjYhQAmIUB7HdGYmAClElCubdz2SAgc0SXGgiHxgMqzYRANITiiqeJlmaG6YZIvl52PmTycgCUFVsxt1aq4ycOJdUklUNxOV6iMQFgyidIcACATkFNQELNiABIwJzqrndKu9dJ6BxnPIGB1M9qv9yeYCYOAAFQwAICA2W3GMu1GQnk2QMT2m2ef7gds7qb9f00X7MvZJCSB/QcCZseDwTEzHqwO8W1tXmBfq63VVGPsjFlMiO/G9YhufDRin+Od3y7RH/88RSgOIIT4yp6EIPWKFmo819XBHaQK9qVaXWyevMhiM3VWO0GmV5XZ73y7uIJJyOd/fUKH908WKsoXoe7OvALpkeI/H43m/Zz2vdjvsnmQd0wuXKuxG0a+Wd+20XA8zazyQk0u7ax2qEcTdr9nGZOi8iidbMA62IplGPuYUJTXJxHcK/knIUCLakSXQQ5IQMSCQQ74AQEu9r6ZJS3cS87OExhCTWTwfU+IBXvA33bGT98dL3x34zoebjYBQ5+MFOWI/sWvjrXfHX/2HUJWOSRgrUmfd2KlOn+qKjmEUalV+bj0PgpTwbrKqK620HejibbT5caLv+F0AuA2FmlhpfFoQue6BuLsLvbzl3szHrz4wnR27O7k6WdJfSXgxquPqMiyGof3Z+VyZa8/wcl7Crd2ia7/RB6LPKWGT4pPSG2a/ffhB9Gp5Y+cVWh1lqg//1lMb/544rzcs+mr7xqVkCpKil//9AzpXJpNjGTMZsuR1U7snVuax2Vnn8VhpGP5sAHhrC7nntL3IdxnhHR7oBaREE40BBETADumA8iGocDTpJCOhWyYSicXyLipsEg5AAlIVRUpP7WTteMC49CI1yQSmhYgNYaA4uY0IiHrQsK9bzrUf568RznEGPT51WfnqlMun60tPTrd3n7SIaLHzYyEmLZrqBK2OPOffn/GOffud385pvfer/dA3doluv7KEYVanChS1YFuiz3YhDK6aVZKqH5A5W6Z2C8YqFUoDk5qAH5AQiHx4AkISQdAAhKczdgpCQDqJAG56VqQakAhBMRecdSI3ZlgA2JG612if78fgYDUkKhGHQPxqB+NWTA1eSFS00Jxhe6W2XQ7cDienpqo1D0W3NRJlQtP4iIDzVWqu9T4Fz8TKyrM1PW01DQybimS9LtZe6fYKakJEnBWoeq4du0qslxJCDspRSFJQKC+6lebaQViAZUSE5sVAlXOkKGCgVJdMEoVDABV+QFZmxVC8gGVbtOAZASg8lAcpCBQaXEiSECAyq6MoCLhlTfeXvgs9W7v/feW182Mgfphbb1HG5s/sgoqXVUNWcjFrtJOR8zR0QDko/Y5lXUCSy7totOuka8U/O7yCkYG8DcJyWLzra330KPw9bHVsTdHNGbI7SSfXM7PxANRVYtkjGWm2khly3YA2m0Pupb0i00iUi5aaWpEFEXUv3iN+heJRkeD1gwAx/a19Ud6jqL6TdXG9Frid6OjAXWXVxbaIf5G5ANHGKUST+ZRemysO0gnUjlk7a2ch2gQwO2PMvtNda30M047sgosmT9xFnWatXJqEfUHuVK6rurLOXuEIYHEMdL93va9TarlmR+In8VERLtf/N3ow0tnt/2L1zINtkuDVZ3CGaQ6k1B+8HT9obPF5aq1rvfqw27njuFwcPjsmNHdmWo/WVs0C+IDIiJyWpnz2utvGTsxSyiPcz5utk7diGiyfTiTPFFK6fqH87nqd7IEdC6tqznONRrmNFqjo4EyM0a2FznklI8xTel1037VebNm7hQR67apH64kF0kr3jdHknJsdpNw4D4oVMWG1SpVmTbM9uSannjd9N7HAOtIW4Q61w2SNixlaJf8GeeByXMv3OM4D0RhBBRv0CT2dYSTPeU60poGgqvCbJ3qe0sKjoQQ75dzfe7vZKevamw4Zo/tWqq9ZSotzZFKKlNmDXdXTpXKdpUgtgRbUV1XsUNAUfuwuEhemzYo2nSJfXSaSdRzwjO6jtDNhjm2j0mi6qS1yjWRVZWppL6JANyZqumeTK4Ymx3NeQBtmS+l2YA6W63QFfQactjUB1d92/IhuSrVhzvGdrwq1ckU+Oeq3yzejbySMPZBkKLUlk4N2ySU6nfcwcpV6SmHlMzTH1wTxVfs3tVPa8JS8NVvAEJ9QAAAAQEQEABAQAAEBAAQEKCmleilhi6kfmH9B0T0LD9NhCpXzQVpXpvtXOLvFqRA9xIlo7vz1/Q8X+3eCZ6AwfsB5UoOVFL6FcfBbctI/vQfn0AFUwCrtqquayNGZnRxWVUbtzYvQAI2FduXr84X15gSDEyhJNc1F0XUUA5dCkZtWImWJ+1c9+cjBs5ZBLR9+SokYNMgrl1p2gJvXbtv79yEBGzKrLeJyzRtpS1CtQeDI2D/4rUgB+q59StBFn+KQnO5hFqjJkkSY2FI2IAl4+WNLSKaOXij7qXgS4KkNuLoaEDDRw/nnz8e32t0YdDGRkJe+t7G03cbRC3a5K+7vCIVC9qgg/2bUMFABdtcBWBuRIh2hLkRNAhY8T60AAhYu1JnkIggYOWb44VEthDuO27jDo24V0hAwHOhI0jABth9dSxgyZlAmZJXm07IqE2qqI6DZUrtMqV+hSIJY6iu6iSma/09wqKk+uDR/u15DDhd0JOZqDmPl8+lWnSU5Vjx/nTnE+PCTcS3CCUnKjtsIqoAAAAASUVORK5CYII=";  //# pulse-scan:allow embedded PNG, not a secret



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
  const dark = "#0a0a0a";
  return {
    subject: "Pulse-HWM sign-in link",
    text: `One-time sign-in link (valid 24h):\n${url}\n\nIf you did not request this, ignore the email.`,
    html: `<div style="max-width:480px;margin:0 auto;padding:24px;background:#0a0a0a;color:#e8e8e8;font-family:sans-serif;border:1px solid #2e2e2e;border-radius:6px">
<img src="https://pulsehwm-cloud.pulsehwm27.workers.dev/branding/logo.png" alt="Pulse-HWM" width="80" height="80" style="display:block;margin:0 auto 12px;border-radius:4px">
<div style="font-family:monospace;font-size:12px;letter-spacing:3px;color:#6a6a6a;text-align:center">PULSE-HWM</div>
<p style="font-family:sans-serif;font-size:15px;color:#e8e8e8">Open this one-time link to finish signing in (valid 24h):</p>
<p style="text-align:center;margin:20px 0"><a href="${safe}" style="background:#ffd400;color:#0a0a0a;font-family:monospace;font-size:14px;font-weight:bold;text-decoration:none;padding:12px 28px;border-radius:4px;display:inline-block">CONFIRM SIGN-IN</a></p>
<p style="color:#6a6a6a;font-family:sans-serif;font-size:12px">If you did not request this, ignore this email.</p>
</div>`,
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
  // a revoked token existing here means it was replayed after rotation —
  // treat the whole family (incl. the current token) as stolen
  if (!row || row.revoked || row.expires_at < nowIso()) {
    if (row && row.revoked) {
      // replayed a rotated token: the family may be stolen — kill it all
      await env.DB.prepare(`UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ?`)
        .bind(row.family_id)
        .run();
    }
    return null;
  }
  // the consume MUST be atomic: condition on the token still being active
  // so two replays racing the same token can never both mint replacements
  const consumed = await env.DB.prepare(
    `UPDATE refresh_tokens SET revoked = 1
     WHERE token_hash = ? AND revoked = 0 AND expires_at > ?`
  )
    .bind(hash, nowIso())
    .run();
  if (!consumed.meta || consumed.meta.changes !== 1) {
    // we lost the race (or the row flipped since the SELECT) — that is
    // token reuse: the family may be stolen, kill it all
    await env.DB.prepare(`UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ?`)
      .bind(row.family_id)
      .run();
    return null;
  }
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
  // returns {email, uid} on success or {error} on failure — the error text
  // travels to the handoff page so failures are never silent guesses
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
    if (!r.ok) return { error: `google token http ${r.status}` };
    const t = await r.json();
    if (t.error) return { error: `google token: ${t.error}` };
    const me = await fetch("https://openidconnect.googleapis.com/v1/userinfo", {
      headers: { Authorization: `Bearer ${t.access_token}` },
    });
    if (!me.ok) return { error: `google userinfo http ${me.status}` };
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
    if (!r.ok) return { error: `github token http ${r.status}` };
    const t = await r.json();
    // GitHub answers 200 with an error body for bad/expired codes
    if (t.error) return { error: `github token: ${t.error}` };
    if (!t.access_token) return { error: "github token: no access_token" };
    // GitHub API rejects requests with no User-Agent (403) — Workers'
    // fetch sends none by default, so set one explicitly
    const ghHeaders = {
      "user-agent": "PulseHWM-Auth",
      accept: "application/vnd.github+json",
    };
    const me = await fetch("https://api.github.com/user", {
      headers: { ...ghHeaders, Authorization: `Bearer ${t.access_token}` },
    });
    if (!me.ok) return { error: `github user http ${me.status}` };
    const p = await me.json();
    let email = p.email;
    if (!email) {
      const em = await fetch("https://api.github.com/user/emails", {
        headers: { ...ghHeaders, Authorization: `Bearer ${t.access_token}` },
      });
      if (em.ok) {
        const list = await em.json();
        const primary = (list || []).find((e) => e.primary) || (list || [])[0];
        email = primary && primary.email;
      }
    }
    return { email: String(email || ""), uid: `github:${p.id}` };
  }
  return { error: "unsupported provider" };
}

// ── the router ────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    try {
      return await route(request, env);
    } catch (err) {
      // log for observability; the client gets a generic 500 — internal
      // messages (D1 schemas, stack traces) must never leave the worker
      console.error("unhandled worker error:", err);
      return fail(500, "internal error");
    }
  },
};

async function route(request, env) {
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
    // server-side sign-out: revoke every active refresh token for the
    // bearer-token user (all sessions on all devices)
    const p = await bearerPayload(env, request);
    if (!p || !p.sub) return fail(401, "invalid credentials");
    await env.DB.prepare(
      `UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0`
    )
      .bind(p.sub)
      .run();
    return json({});
  }
  if (path === "/auth/v1/user" && method === "GET") {
    const p = await bearerPayload(env, request);
    if (!p) return fail(401, "invalid credentials");
    // the client reads `id` (session._adopt), keep email as-is
    return json({ id: p.sub, email: p.email });
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

  // ── PostgREST-shaped storage (ownership fenced to the JWT user) ────
  if (path.startsWith("/rest/v1/")) {
    return rest(request, env, path.slice("/rest/v1/".length));
  }

  return fail(404, "no such endpoint");
}

// ── signup ─────────────────────────────────────────────────────────────

// redirect_to MUST be the app deep link: accepting arbitrary URLs would
// turn the emailed one-time links into an open redirect / phishing vector
function safeRedirectTo(v) {
  const s = String(v || "");
  return s.startsWith("pulsehwm://auth-callback") ? s : "pulsehwm://auth-callback";
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
      // NO prompt param: Google's v3 account-chooser spins forever on the
      // consent handoff when prompt=select_account is set (observed live).
      // Without it a signed-in session goes straight to consent/consented.
    });
    // Google wants space-separated scopes as %20 — URLSearchParams emits
    // "+" and Google's consent handoff hangs on it (observed live).
    authorizeUrl.search = authorizeUrl.search.replace(
      "scope=openid+email+profile",
      "scope=openid%20email%20profile"
    );
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

// ── custom-scheme handoff ─────────────────────────────────────────────

// Browsers can't render a custom scheme: a bare 3xx to pulsehwm:// leaves
// Firefox/Chrome spinning on a blank tab forever. Serve a tiny HTML page
// that navigates to the scheme instead, with a visible manual link.
// provider-supplied error text (errDesc) must never reach the HTML raw,
// so every dynamic note is escaped here.
const HTML_ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => HTML_ENTITIES[c]);
}

function handoffHtml(target, note) {
  // only our scheme / https may be handed off (redirect_to comes from URLs)
  const ok = /^pulsehwm:|^https:\/\//i.test(target);
  const safe = (ok ? target : "pulsehwm://auth-callback").replace(/["<>\\]/g, "");
  // notes are plain text callers-by-contract: escape fully; entities used
  // in caller notes are written with ASCII text instead so nothing double-
  // escapes
  const msg = note ? escapeHtml(note) : "Returning to Pulse-HWM&hellip;";
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Pulse-HWM</title>` +
    `<style>body{background:#0b0b12;color:#d8d8e8;font-family:monospace;display:flex;` +
    `align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}` +
    `a{color:#7fd18f}button{background:#1a1a2e;color:#d8d8e8;border:1px solid #3a3a5e;` +
    `font-family:monospace;font-size:14px;padding:8px 16px;cursor:pointer;margin-top:12px}` +
    `</style></head><body><div><p>${msg}</p>` +
    `<p><a href="${safe}">If nothing happened, click here</a></p>` +
    `<button onclick="go()">Open Pulse-HWM</button></div>` +
    // location.replace to a custom scheme is silently blocked by some
    // browsers without a user gesture; location.href + a real click
    // handler give the handler two chances to fire
    `<script>function go(){window.location.href="${safe}";}` +
    `setTimeout(go,400);</script></body></html>`;
  return new Response(html, {
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

async function oauthCallback(env, url, provider) {
  const state = url.searchParams.get("state") || "";
  const code = url.searchParams.get("code") || "";
  const errDesc = url.searchParams.get("error_description") || url.searchParams.get("error") || "";
  const back = "pulsehwm://auth-callback";
  if (errDesc) {
    return handoffHtml(`${back}?error_description=${encodeURIComponent(errDesc)}`, `Sign-in failed: ${errDesc}`);
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
  if (profile.error)
    return handoffHtml(
      `${back}?error_description=${encodeURIComponent(profile.error)}`,
      `Sign-in failed: ${profile.error}`
    );
  if (!profile.email)
    return handoffHtml(`${back}?error_description=${encodeURIComponent("provider did not share an email")}`, "Provider did not share an email - close this tab and try again.");
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
  return handoffHtml(`${back}?code=${encodeURIComponent(appCode)}`);
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
