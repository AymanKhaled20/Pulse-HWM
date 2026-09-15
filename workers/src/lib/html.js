// Custom-scheme handoff HTML (moved verbatim).

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
  // only our EXACT scheme target / https may be handed off (redirect_to
  // comes from URLs); a suffixed variant (pulsehwm://auth-callback.evil)
  // is still registered to Pulse on Windows and could interrupt an active
  // sign-in with junk params
  const matchesCallback = /^pulsehwm:\/\/auth-callback(\?|$)/i.test(target);
  const ok = matchesCallback || /^https:\/\//i.test(target);
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

export { escapeHtml, handoffHtml };
