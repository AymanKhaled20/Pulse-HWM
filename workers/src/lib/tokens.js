// Access + rotating refresh tokens (moved verbatim).

import { randToken } from "./encoding.js";
import { sha256Hex } from "./hash.js";
import { hmacJwtSign } from "./jwt.js";
import { isoIn, nowIso } from "./respond.js";

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

export { mintTokens, refreshRotate };
