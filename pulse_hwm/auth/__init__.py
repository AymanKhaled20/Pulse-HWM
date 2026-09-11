"""Accounts / cloud sync for Pulse-HWM.

Everything here talks to Supabase over HTTPS with the PUBLISHABLE key,
which is safe precisely because Row Level Security fences every table
to auth.uid(). The refresh token never touches a file — it lives in
Windows Credential Manager (see token_store.py).
"""
