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
  await env.DB.prepare(`DELETE FROM intent_codes WHERE code = ?`)
    .bind(authCode)
    .run();
  // PKCE: the verifier the parked app holds must hash to the stored challenge
  const challenge = await pkceChallenge(verifier || "");
  if (challenge !== row.code_challenge) return null;
  return row;
}

export { makeIntent, consumeIntent };
