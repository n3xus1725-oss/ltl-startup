"""Symmetric encryption for stored OAuth credentials using Fernet.

If CREDENTIALS_ENCRYPTION_KEY is not set, falls back to base64 encoding
(better than plaintext, but encryption is strongly recommended in production).
"""
import base64
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _get_fernet():
    """Return a Fernet instance if key is configured, else None."""
    try:
        from packages.domain.config import get_settings
        key = get_settings().CREDENTIALS_ENCRYPTION_KEY
        if key:
            from cryptography.fernet import Fernet
            return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        logger.warning("Fernet encryption not available: %s", e)
    return None


def encrypt_credentials(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """Encrypt credential dict. Stores as {encrypted: <base64-ciphertext>} if Fernet available."""
    f = _get_fernet()
    if f:
        plaintext = json.dumps(credentials).encode()
        ciphertext = f.encrypt(plaintext)
        return {"_fernet": ciphertext.decode()}
    # Fallback: base64 encode (not encrypted, but at least not raw plaintext)
    return {"_b64": base64.b64encode(json.dumps(credentials).encode()).decode()}


def decrypt_credentials(stored: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt credential dict stored by encrypt_credentials."""
    if not stored:
        return {}
    if "_fernet" in stored:
        f = _get_fernet()
        if f:
            try:
                plaintext = f.decrypt(stored["_fernet"].encode())
                return json.loads(plaintext)
            except Exception as e:
                logger.error("Failed to decrypt credentials: %s", e)
                return {}
    if "_b64" in stored:
        try:
            return json.loads(base64.b64decode(stored["_b64"]).decode())
        except Exception:
            return {}
    # Legacy plaintext — return as-is
    return stored
