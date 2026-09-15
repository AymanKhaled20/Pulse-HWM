"""Accounts / cloud sync / updates for Pulse-HWM.

Everything here talks to the Pulse cloud Worker (Cloudflare Workers + D1)
over HTTPS with a PUBLISHABLE key — safe because the Worker fences every
table to the JWT subject (the RLS stand-in) and keeps every real secret
server-side. The refresh token never touches a file — it lives in
Windows Credential Manager (see token_store.py).
"""
