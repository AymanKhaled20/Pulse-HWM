// SHA-256 / PBKDF2 / PKCE challenge helpers (moved verbatim).

import { enc } from "./encoding.js";

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

export { sha256Hex, pkceChallenge, pbkdf2Hex, hashPassword, ITER };
