// Brevo email + embedded branding logo (moved verbatim).

// ── email (Brevo HTTP API) ─────────────────────────────────────────────

// the branded icon served from /branding/logo.png (16-color quantized,
// 160px ≈ 4 KB) — embedded as base64 so the worker needs no external asset
const EMAIL_LOGO_PNG_B64 =
 "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";  //# pulse-scan:allow embedded PNG, not a secret



async function sendMail(env, to, msg) {
  if (!env.BREVO_API_KEY || !env.SENDER_EMAIL) return false;
  const r = await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: {
      "api-key": env.BREVO_API_KEY,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({
      sender: { email: env.SENDER_EMAIL, name: env.SENDER_NAME || "Pulse-HWM" },
      to: [{ email: to }],
      subject: msg.subject,
      textContent: msg.text,
      htmlContent: msg.html,
    }),
  });
  return r.ok;
}

function linkMail(url) {
  const safe = url.replace(/&/g, "&amp;");
  const dark = "#0a0a0a";
  return {
    subject: "Pulse-HWM sign-in link",
    text: `One-time sign-in link (valid 24h):\n${url}\n\nIf you did not request this, ignore the email.`,
    html: `<div style="max-width:480px;margin:0 auto;padding:24px;background:#0a0a0a;color:#e8e8e8;font-family:sans-serif;border:1px solid #2e2e2e;border-radius:6px">
<img src="https://pulsehwm-cloud.pulsehwm27.workers.dev/branding/logo.png" alt="Pulse-HWM" width="80" height="80" style="display:block;margin:0 auto 12px;border-radius:4px">
<div style="font-family:monospace;font-size:12px;letter-spacing:3px;color:#6a6a6a;text-align:center">PULSE-HWM</div>
<p style="font-family:sans-serif;font-size:15px;color:#e8e8e8">Open this one-time link to finish signing in (valid 24h):</p>
<p style="text-align:center;margin:20px 0"><a href="${safe}" style="background:#ffd400;color:#0a0a0a;font-family:monospace;font-size:14px;font-weight:bold;text-decoration:none;padding:12px 28px;border-radius:4px;display:inline-block">CONFIRM SIGN-IN</a></p>
<p style="color:#6a6a6a;font-family:sans-serif;font-size:12px">If you did not request this, ignore this email.</p>
</div>`,
  };
}

export { EMAIL_LOGO_PNG_B64, sendMail, linkMail };
