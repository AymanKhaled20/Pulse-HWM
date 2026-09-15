// Account activity tracking for update entitlement.
//
// Updates are for REGISTERED + ACTIVE accounts (product rule): the gate is
// (a) a valid JWT — access tokens live 1h, refresh tokens 30d, so an install
// untouched for a month can't refresh and must sign in again — plus (b) this
// explicit activity window (UPDATE_ACTIVITY_DAYS, default 90).
//
// Write budget matters (D1 free tier): last_seen_at is read on every gated
// call but only WRITTEN when it is at least ACTIVITY_TOUCH_MIN_S old.

import { nowIso } from "./respond.js";

export const ACTIVITY_TOUCH_MIN_S = 12 * 3600;

// Single throttled read(+write): updates last_seen_at only when stale, and
// reports whether the account is inside the active window. A first-ever
// call (no row yet) counts as active.
export async function touchAndCheck(env, uid, windowDays) {
  const row = await env.DB.prepare(
    `SELECT last_seen_at FROM user_update_state WHERE user_id = ?`
  )
    .bind(uid)
    .first();
  const now = Date.now();
  if (!row) {
    await env.DB.prepare(
      `INSERT INTO user_update_state (user_id, last_seen_at, last_check_at, last_seen_version)
       VALUES (?, ?, '', '')`
    )
      .bind(uid, nowIso())
      .run();
    return true;
  }
  const seenMs = Date.parse(row.last_seen_at || "");
  // decide entitlement FIRST from the stored value; never refresh the
  // window for an account that just failed the gate, or a single retry
  // would re-arm the window and defeat the gate entirely
  const active =
    !windowDays || windowDays <= 0
      ? true
      : !Number.isNaN(seenMs) && now - seenMs <= windowDays * 86400 * 1000;
  const stale = Number.isNaN(seenMs) || now - seenMs >= ACTIVITY_TOUCH_MIN_S * 1000;
  if (active && stale) {
    await env.DB.prepare(
      `UPDATE user_update_state SET last_seen_at = ?, last_check_at = ? WHERE user_id = ?`
    )
      .bind(nowIso(), nowIso(), uid)
      .run();
  }
  return active;
}
