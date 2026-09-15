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
  // the consume AND the replacement INSERT live in ONE atomic D1 batch:
  // per-statement run() calls are separate transactions, so a racing replay
  // could revoke the family between consume and insert and leave that fresh
  // replacement active. Batch guarantees the whole state change is atomic.
  const refresh = randToken();
  const replacementHash = await sha256Hex(refresh);
  const access = await hmacJwtSign(env.JWT_SECRET, {
    sub: row.user_id,
    email: row.email,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + Number(env.ACCESS_TTL_S || 3600),
  });
  const consumed = await env.DB.batch([
    env.DB.prepare(
      `UPDATE refresh_tokens SET revoked = 1
       WHERE token_hash = ? AND revoked = 0 AND expires_at > ?`
    ).bind(hash, nowIso()),
    env.DB.prepare(
      `INSERT INTO refresh_tokens (token_hash, user_id, family_id, expires_at, revoked, created_at)
       VALUES (?, ?, ?, ?, 0, ?)`
    )
      .bind(
        replacementHash,
        row.user_id,
        row.family_id,
        isoIn(Number(env.REFRESH_TTL_S || 2592000)),
        nowIso()
      ),
  ]);
  if (!consumed || !consumed[0] || consumed[0].meta?.changes !== 1) {
    // we lost the race (or the row flipped since the SELECT) — that is
    // token reuse: the family may be stolen, kill it all
    await env.DB.prepare(`UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ?`)
      .bind(row.family_id)
      .run();
    return null;
  }
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
