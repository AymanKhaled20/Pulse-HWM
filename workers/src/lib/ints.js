// One-time intent codes (email links / OAuth app codes).

import { randToken } from "./encoding.js";
import { pkceChallenge } from "./hash.js";
import { isoIn, nowIso } from "./respond.js";

const CODE_TTL_S = 24 * 3600; // email links / app codes

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
  // PKCE must be verified BEFORE the one-time consume: a bad verifier has
  // to leave the valid intent available for a retry with the right one
  const challenge = await pkceChallenge(verifier || "");
  if (challenge !== row.code_challenge) return null;
  // deleting conditionally on the stored challenge keeps concurrent valid
  // requests one-time (only the row with the exact challenge is consumed)
  const deleted = await env.DB.prepare(
    `DELETE FROM intent_codes WHERE code = ? AND code_challenge = ?`
  )
    .bind(authCode, row.code_challenge)
    .run();
  if (!deleted.meta || deleted.meta.changes !== 1) return null;
  return row;
}

export { makeIntent, consumeIntent };
