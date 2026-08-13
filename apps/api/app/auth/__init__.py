"""Local-account authentication for a self-hosted deployment.

Argon2id password hashing, opaque HttpOnly session cookies whose secrets are only ever
stored as SHA-256 fingerprints, a session-bound CSRF synchroniser token, and
project-level authorisation.
"""
