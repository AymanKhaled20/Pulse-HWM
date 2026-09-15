// Encoding + randomness helpers (moved verbatim from worker.js).

const enc = new TextEncoder();

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

export { enc, b64url, b64urlJson, fromB64url, randToken, randHex };
