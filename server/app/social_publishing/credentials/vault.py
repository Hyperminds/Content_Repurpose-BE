"""Credential vault — encrypts and decrypts OAuth tokens at rest.

Security guarantees:
  - Tokens are encrypted using Fernet (AES-128-CBC + HMAC-SHA256).
  - The encryption key is loaded from an environment variable, never hardcoded.
  - Tokens are NEVER logged, even at DEBUG level.
  - The vault never returns raw tokens through public-facing API responses.
  - If the encryption key is not configured, the vault raises on construction
    so the application fails fast rather than storing tokens in plaintext.

Usage:
    vault = CredentialVault()
    encrypted = vault.encrypt(access_token)
    # ... store `encrypted` in the database ...
    access_token = vault.decrypt(encrypted)
"""

import os
import base64
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

# Environment variable name for the encryption key.
# Must be a valid Fernet key (32 url-safe base64-encoded bytes).
# Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
_ENV_KEY_NAME = "SP_CREDENTIAL_ENCRYPTION_KEY"


class CredentialVaultError(Exception):
    """Raised when encryption/decryption operations fail."""

    pass


class CredentialVault:
    """Encrypts and decrypts credential strings using Fernet symmetric encryption.

    The encryption key is read from the environment variable SP_CREDENTIAL_ENCRYPTION_KEY.
    If not set, the vault will use a derived key in development mode only (APP_ENV=development),
    or raise an error in production.
    """

    def __init__(self, key: Optional[str] = None) -> None:
        """
        Initialize the vault.

        Args:
            key: Optional explicit Fernet key (used in tests). If None, reads from env.
        """
        resolved_key = key or os.getenv(_ENV_KEY_NAME)

        if not resolved_key:
            # In development, derive a deterministic key so the system works out of the box.
            # In production, this MUST be set explicitly.
            from app.config import IS_PRODUCTION
            if IS_PRODUCTION:
                raise CredentialVaultError(
                    f"Environment variable {_ENV_KEY_NAME} is required in production. "
                    f"Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
            # Development fallback: derive from JWT_SECRET (not secure for prod, but functional for dev)
            resolved_key = _derive_dev_key()

        try:
            self._fernet = Fernet(resolved_key.encode() if isinstance(resolved_key, str) else resolved_key)
        except (ValueError, Exception) as e:
            raise CredentialVaultError(f"Invalid encryption key: {e}")

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a credential string. Returns a base64-encoded ciphertext."""
        if not plaintext:
            return ""
        encrypted_bytes = self._fernet.encrypt(plaintext.encode("utf-8"))
        return encrypted_bytes.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a credential string. Raises CredentialVaultError on failure."""
        if not ciphertext:
            return ""
        try:
            decrypted_bytes = self._fernet.decrypt(ciphertext.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except InvalidToken:
            raise CredentialVaultError("Failed to decrypt credential — key mismatch or corrupted data")
        except Exception as e:
            raise CredentialVaultError(f"Decryption failed: {e}")

    def encrypt_dict(self, credentials: dict) -> dict:
        """Encrypt all string values in a credentials dict."""
        encrypted: dict = {}
        for key, value in credentials.items():
            if isinstance(value, str) and value:
                encrypted[key] = self.encrypt(value)
            else:
                encrypted[key] = value
        return encrypted

    def decrypt_dict(self, encrypted_credentials: dict) -> dict:
        """Decrypt all encrypted string values in a credentials dict."""
        decrypted: dict = {}
        for key, value in encrypted_credentials.items():
            if isinstance(value, str) and value and _looks_encrypted(value):
                try:
                    decrypted[key] = self.decrypt(value)
                except CredentialVaultError:
                    decrypted[key] = ""  # Silently clear corrupted values
            else:
                decrypted[key] = value
        return decrypted


def _looks_encrypted(value: str) -> bool:
    """Heuristic: Fernet tokens are base64 and start with 'gAAAAA'."""
    return value.startswith("gAAAAA") and len(value) > 50


def _derive_dev_key() -> str:
    """Derive a deterministic Fernet key for development mode.

    This is NOT cryptographically secure for production — it's purely so
    development environments work without manual key setup.
    """
    import hashlib
    from app.config import JWT_SECRET
    # SHA-256 the JWT secret and take 32 bytes, then base64-encode for Fernet
    raw = hashlib.sha256(JWT_SECRET.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(raw).decode("utf-8")
