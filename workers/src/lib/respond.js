// Tiny response + clock helpers (moved verbatim).

function nowIso() {
  return new Date().toISOString().replace("Z", "+00:00");
}

function isoIn(seconds) {
  return new Date(Date.now() + seconds * 1000).toISOString().replace("Z", "+00:00");
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

export { nowIso, isoIn, json, fail };
