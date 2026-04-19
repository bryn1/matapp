#!/usr/bin/env python3
"""
auth.py — Encryption and authentication utilities.

Security model:
- Passwords hashed with bcrypt (cost 12)
- Per-user AES-256-GCM encryption derived from password via PBKDF2-SHA256
- Key is NEVER stored — only lives in server memory during session
- Admin cannot decrypt other users' data
- Data loss on forgotten password is intentional (by-design privacy guarantee)
"""
import base64
import json
import secrets
from typing import Any

import bcrypt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# 260k iterations ≈ 0.35s on i5-5200U; above NIST SP800-132 minimum
PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def new_salt() -> str:
    """Random base64-encoded 32-byte salt for key derivation."""
    return base64.b64encode(secrets.token_bytes(32)).decode()


def derive_key(password: str, salt_b64: str) -> bytes:
    """Derive 256-bit AES key from password + stored salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=base64.b64decode(salt_b64),
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_json(data: Any, key: bytes) -> str:
    """Encrypt any JSON-serialisable value → base64(nonce‖ciphertext)."""
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(data, ensure_ascii=False).encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_json(blob: str, key: bytes) -> Any:
    """Decrypt base64(nonce‖ciphertext) → original value."""
    raw = base64.b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    return json.loads(AESGCM(key).decrypt(nonce, ct, None).decode("utf-8"))
