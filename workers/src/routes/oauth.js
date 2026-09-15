// OAuth authorize + provider callback (moved verbatim).

import { fail, json, isoIn, nowIso } from "../lib/respond.js";
import { randToken } from "../lib/encoding.js";
import { sha256Hex } from "../lib/hash.js";
import { makeIntent } from "../lib/ints.js";
import { providerProfile } from "../lib/providers.js";
import { handoffHtml } from "../lib/html.js";

// â”€â”€ OAuth provider authorize/callback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function oauthAuthorize(env, url, origin) {
  const provider = url.searchParams.get("provider") || "";
  const challenge = url.searchParams.get("code_challenge") || "";
  if (!provider || !challenge) return fail(400, "provider and code_challenge required");
  const state = randToken();
  await env.DB.prepare(
    `INSERT INTO oauth_states (state, provider, code_challenge, app_code, expires_at)
     VALUES (?, ?, ?, '', ?)`
  )
    .bind(state, provider, challenge, isoIn(900))
    .run();
  let authorizeUrl;
  if (provider === "google") {
    authorizeUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    authorizeUrl.search = new URLSearchParams({
      client_id: env.GOOGLE_CLIENT_ID || "",
      redirect_uri: `${origin}/cb/google`,
      response_type: "code",
      scope: "openid email profile",
      state,
      // NO prompt param: Google's v3 account-chooser spins forever on the
      // consent handoff when prompt=select_account is set (observed live).
      // Without it a signed-in session goes straight to consent/consented.
    });
    // Google wants space-separated scopes as %20 — URLSearchParams emits
    // "+" and Google's consent handoff hangs on it (observed live).
    authorizeUrl.search = authorizeUrl.search.replace(
      "scope=openid+email+profile",
      "scope=openid%20email%20profile"
    );
  } else if (provider === "github") {
    authorizeUrl = new URL("https://github.com/login/oauth/authorize");
    authorizeUrl.search = new URLSearchParams({
      client_id: env.GITHUB_CLIENT_ID || "",
      redirect_uri: `${origin}/cb/github`,
      scope: "read:user user:email",
      state,
    });
  } else {
    return fail(400, "unsupported provider");
  }
  return Response.redirect(authorizeUrl.toString(), 302);
}


async function oauthCallback(env, url, provider) {
  const state = url.searchParams.get("state") || "";
  const code = url.searchParams.get("code") || "";
  const errDesc = url.searchParams.get("error_description") || url.searchParams.get("error") || "";
  const back = "pulsehwm://auth-callback";
  if (errDesc) {
    return handoffHtml(`${back}?error_description=${encodeURIComponent(errDesc)}`, `Sign-in failed: ${errDesc}`);
  }
  const row = await env.DB.prepare(`SELECT * FROM oauth_states WHERE state = ?`)
    .bind(state)
    .first();
  if (row && row.provider === provider) {
    await env.DB.prepare(`DELETE FROM oauth_states WHERE state = ?`).bind(state).run();
  }
  if (!code || !row || row.provider !== provider) {
    return fail(400, "unknown or expired sign-in state");
  }
  const profile = await providerProfile(env, provider, code, url.origin);
  if (profile.error)
    return handoffHtml(
      `${back}?error_description=${encodeURIComponent(profile.error)}`,
      `Sign-in failed: ${profile.error}`
    );
  if (!profile.email)
    return handoffHtml(`${back}?error_description=${encodeURIComponent("provider did not share an email")}`, "Provider did not share an email - close this tab and try again.");
  const email = profile.email.toLowerCase();

  let user = await env.DB.prepare(`SELECT id, email FROM users WHERE email = ?`)
    .bind(email)
    .first();
  if (!user) {
    // stable id from the provider identity (re-signing in recreates it)
    const id = "u" + (await sha256Hex(profile.uid)).slice(0, 32);
    user = { id, email };
    await env.DB.prepare(
      `INSERT OR IGNORE INTO users (id, email, password_hash, provider, email_verified, created_at)
       VALUES (?, ?, NULL, ?, 1, ?)`
    )
      .bind(id, email, provider, nowIso())
      .run();
  }
  // the app code is PKCE-bound like any other intent code
  const appCode = await makeIntent(env, user.id, `oauth-${provider}`, row.code_challenge);
  return handoffHtml(`${back}?code=${encodeURIComponent(appCode)}`);
}

export { oauthAuthorize, oauthCallback };

