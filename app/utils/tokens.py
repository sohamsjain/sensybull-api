"""Secure token helpers for email verification and password reset.

Design:
- Raw tokens are 256-bit URL-safe strings (secrets.token_urlsafe(32)).
- Only the SHA-256 hash is persisted; the raw token exists only in the
  delivered email body. An attacker with DB read access cannot use tokens.
- Comparison is done in constant time via secrets.compare_digest to avoid
  timing attacks on hash lookup.
"""
import hashlib
import secrets


def generate_token() -> str:
    """Generate a cryptographically secure URL-safe token (~43 chars)."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """Return the lowercase hex SHA-256 of the raw token (64 chars)."""
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def verify_token(raw: str, stored_hash: str) -> bool:
    """Constant-time compare of `raw` against a previously stored hash."""
    if not raw or not stored_hash:
        return False
    return secrets.compare_digest(hash_token(raw), stored_hash)
