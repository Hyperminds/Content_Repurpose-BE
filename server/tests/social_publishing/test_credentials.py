"""Tests for the credential vault — encryption, decryption, key handling."""

import pytest
from cryptography.fernet import Fernet

from app.social_publishing.credentials.vault import (
    CredentialVault,
    CredentialVaultError,
)


@pytest.fixture
def vault():
    """Create a vault with a known test key."""
    test_key = Fernet.generate_key().decode()
    return CredentialVault(key=test_key)


@pytest.fixture
def test_key():
    return Fernet.generate_key().decode()


class TestCredentialVault:
    def test_encrypt_decrypt_roundtrip(self, vault):
        plaintext = "my_secret_access_token_12345"
        encrypted = vault.encrypt(plaintext)
        assert encrypted != plaintext
        assert len(encrypted) > 50
        decrypted = vault.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_empty_string(self, vault):
        assert vault.encrypt("") == ""

    def test_decrypt_empty_string(self, vault):
        assert vault.decrypt("") == ""

    def test_different_encryptions_differ(self, vault):
        """Same plaintext produces different ciphertexts (Fernet uses random IV)."""
        text = "same_token"
        enc1 = vault.encrypt(text)
        enc2 = vault.encrypt(text)
        assert enc1 != enc2  # Different IVs

    def test_decrypt_with_wrong_key_fails(self, test_key):
        vault1 = CredentialVault(key=test_key)
        encrypted = vault1.encrypt("secret")

        wrong_key = Fernet.generate_key().decode()
        vault2 = CredentialVault(key=wrong_key)
        with pytest.raises(CredentialVaultError):
            vault2.decrypt(encrypted)

    def test_encrypt_dict(self, vault):
        creds = {
            "access_token": "at_123456",
            "refresh_token": "rt_abcdef",
            "token_type": "Bearer",
        }
        encrypted = vault.encrypt_dict(creds)
        assert encrypted["access_token"] != "at_123456"
        assert encrypted["refresh_token"] != "rt_abcdef"
        assert encrypted["token_type"] != "Bearer"

    def test_decrypt_dict(self, vault):
        creds = {
            "access_token": "at_123456",
            "refresh_token": "rt_abcdef",
            "token_type": "Bearer",
        }
        encrypted = vault.encrypt_dict(creds)
        decrypted = vault.decrypt_dict(encrypted)
        assert decrypted == creds

    def test_encrypt_dict_preserves_non_strings(self, vault):
        creds = {"access_token": "secret", "expires_in": 3600, "active": True}
        encrypted = vault.encrypt_dict(creds)
        assert encrypted["expires_in"] == 3600
        assert encrypted["active"] is True

    def test_invalid_key_raises(self):
        with pytest.raises(CredentialVaultError):
            CredentialVault(key="not_a_valid_fernet_key")

    def test_decrypt_corrupted_data_raises(self, vault):
        with pytest.raises(CredentialVaultError):
            vault.decrypt("gAAAAA_corrupted_data_that_is_long_enough_to_look_encrypted_but_isnt_valid")

    def test_tokens_never_in_model(self, vault):
        """Verify that the domain model SocialAccount has no token field."""
        from app.social_publishing.domain.models import SocialAccount
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(SocialAccount)}
        assert "access_token" not in field_names
        assert "refresh_token" not in field_names
        assert "encrypted_credentials" not in field_names
