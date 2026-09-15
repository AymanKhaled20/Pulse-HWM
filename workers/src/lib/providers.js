// Google/GitHub OAuth profile exchange (moved verbatim).

// ── provider exchanges ─────────────────────────────────────────────────

async function providerProfile(env, provider, code, origin) {
  // returns {email, uid} on success or {error} on failure — the error text
  // travels to the handoff page so failures are never silent guesses
  if (provider === "google") {
    const r = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: env.GOOGLE_CLIENT_ID,
        client_secret: env.GOOGLE_CLIENT_SECRET,
        redirect_uri: `${origin}/cb/google`,
        grant_type: "authorization_code",
      }),
    });
    if (!r.ok) return { error: `google token http ${r.status}` };
    const t = await r.json();
    if (t.error) return { error: `google token: ${t.error}` };
    const me = await fetch("https://openidconnect.googleapis.com/v1/userinfo", {
      headers: { Authorization: `Bearer ${t.access_token}` },
    });
    if (!me.ok) return { error: `google userinfo http ${me.status}` };
    const p = await me.json();
    return { email: String(p.email || ""), uid: `google:${p.sub}` };
  }
  if (provider === "github") {
    const r = await fetch("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({
        code,
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        redirect_uri: `${origin}/cb/github`,
      }),
    });
    if (!r.ok) return { error: `github token http ${r.status}` };
    const t = await r.json();
    // GitHub answers 200 with an error body for bad/expired codes
    if (t.error) return { error: `github token: ${t.error}` };
    if (!t.access_token) return { error: "github token: no access_token" };
    // GitHub API rejects requests with no User-Agent (403) — Workers'
    // fetch sends none by default, so set one explicitly
    const ghHeaders = {
      "user-agent": "PulseHWM-Auth",
      accept: "application/vnd.github+json",
    };
    const me = await fetch("https://api.github.com/user", {
      headers: { ...ghHeaders, Authorization: `Bearer ${t.access_token}` },
    });
    if (!me.ok) return { error: `github user http ${me.status}` };
    const p = await me.json();
    let email = p.email;
    if (!email) {
      const em = await fetch("https://api.github.com/user/emails", {
        headers: { ...ghHeaders, Authorization: `Bearer ${t.access_token}` },
      });
      if (em.ok) {
        const list = await em.json();
        const primary = (list || []).find((e) => e.primary) || (list || [])[0];
        email = primary && primary.email;
      }
    }
    return { email: String(email || ""), uid: `github:${p.id}` };
  }
  return { error: "unsupported provider" };
}

export { providerProfile };
