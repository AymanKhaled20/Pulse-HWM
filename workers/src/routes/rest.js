// PostgREST-shaped data tables, fenced to the JWT user (moved verbatim).

import { bearerPayload } from "../lib/jwt.js";
import { fail, json } from "../lib/respond.js";
import { touchAndCheck } from "../lib/activity.js";

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
  // activity signal for update entitlement: synced installs are active
  // users — touch is throttled (≥12h between writes), never gated
  await touchAndCheck(env, uid, 0);

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

export { rest };
