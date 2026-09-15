// HS256 JWT sign/verify + bearer extraction (moved verbatim).

import { enc, b64urlJson, fromB64url } from "./encoding.js";

async function hmacJwtSign(secret, payload) {
  const head = b64urlJson({ alg: "HS256", typ: "JWT" });
  const body = b64urlJson(payload);
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(`${head}.${body}`));
  return `${head}.${body}.${b64url(sig)}`;
}

async function hmacJwtVerify(secret, token) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
  );
  let ok;
  try {
    ok = await crypto.subtle.verify(
      "HMAC",
      key,
      fromB64url(parts[2]),
      enc.encode(`${parts[0]}.${parts[1]}`)
    );
  } catch {
    return null;
  }
  if (!ok) return null;
  try {
    const payload = JSON.parse(new TextDecoder().decode(fromB64url(parts[1])));
    // expired access tokens must never authenticate anything, even though
    // the signature is still valid
    if (!payload || typeof payload.exp !== "number" || payload.exp * 1000 <= Date.now()) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

async function bearerPayload(env, request) {
  const auth = request.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) return null;
  return hmacJwtVerify(env.JWT_SECRET, auth.slice(7));
}

export { hmacJwtSign, hmacJwtVerify, bearerPayload };
