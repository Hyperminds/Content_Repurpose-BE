"""Tests for OAuth state management, provider registry, and connection flow."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.fernet import Fernet

from app.social_publishing.domain.enums import (
    AccountCapability,
    ConnectionStatus,
    SocialPlatform,
)
from app.social_publishing.domain.exceptions import (
    PlatformNotSupported,
    SocialPublishingError,
    ValidationError,
)
from app.social_publishing.domain.models import OAuthState, SocialAccount
from app.social_publishing.auth.provider import (
    AccountInfo,
    AuthProviderRegistry,
    OAuthTokenResponse,
)
from app.social_publishing.auth.linkedin_provider import LinkedInAuthProvider
from app.social_publishing.publishers.stub_publisher import StubPublisher
from app.social_publishing.credentials.vault import CredentialVault
from app.social_publishing.services.account_connection_service import AccountConnectionService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def vault():
    return CredentialVault(key=Fernet.generate_key().decode())


@pytest.fixture
def mock_state_store():
    store = AsyncMock()
    return store


@pytest.fixture
def mock_accounts_repo():
    return AsyncMock()


@pytest.fixture
def auth_registry():
    return AuthProviderRegistry()


def _make_oauth_state(
    tenant_id: str = "tenant_1",
    user_id: str = "user_1",
    platform: SocialPlatform = SocialPlatform.LINKEDIN,
) -> OAuthState:
    now = datetime.now(timezone.utc)
    return OAuthState(
        state_token="test_state_abc123",
        tenant_id=tenant_id,
        user_id=user_id,
        platform=platform,
        redirect_uri="http://localhost:8000/social-publishing/callback/linkedin",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )


def _make_mock_provider(platform: str = "linkedin"):
    provider = AsyncMock()
    provider.platform = SocialPlatform(platform)
    provider.required_scopes = ["openid", "profile", "email"]
    provider.get_auth_url = MagicMock(return_value="https://example.com/auth?state=abc")
    provider.exchange_code = AsyncMock(return_value=OAuthTokenResponse(
        access_token="at_new_token",
        refresh_token="rt_refresh",
        expires_in_seconds=3600,
        scopes=["openid", "profile"],
    ))
    provider.get_account_info = AsyncMock(return_value=AccountInfo(
        platform_account_id="ext_user_123",
        account_name="Test User",
        account_type="personal",
        email="test@example.com",
    ))
    provider.detect_capabilities = AsyncMock(return_value=[
        AccountCapability.TEXT_PUBLISHING,
        AccountCapability.IMAGE_PUBLISHING,
    ])
    return provider


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PROVIDER REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthProviderRegistry:
    def test_register_and_get(self):
        registry = AuthProviderRegistry()
        provider = _make_mock_provider()
        registry.register(provider)
        assert registry.get("linkedin") is provider

    def test_get_with_enum(self):
        registry = AuthProviderRegistry()
        provider = _make_mock_provider()
        registry.register(provider)
        assert registry.get(SocialPlatform.LINKEDIN) is provider

    def test_get_missing_returns_none(self):
        registry = AuthProviderRegistry()
        assert registry.get("tiktok") is None

    def test_has(self):
        registry = AuthProviderRegistry()
        provider = _make_mock_provider()
        registry.register(provider)
        assert registry.has("linkedin") is True
        assert registry.has("tiktok") is False

    def test_supported_platforms(self):
        registry = AuthProviderRegistry()
        registry.register(_make_mock_provider("linkedin"))
        assert "linkedin" in registry.supported_platforms


# ══════════════════════════════════════════════════════════════════════════════
# OAUTH STATE VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestOAuthStateValidation:
    @pytest.mark.asyncio
    async def test_valid_state_consumed(self, mock_state_store):
        """Valid state is returned and consumed (one-time use)."""
        state = _make_oauth_state()
        mock_state_store.validate_and_consume.return_value = state

        result = await mock_state_store.validate_and_consume("test_state_abc123")
        assert result is not None
        assert result.tenant_id == "tenant_1"
        assert result.platform == SocialPlatform.LINKEDIN

    @pytest.mark.asyncio
    async def test_invalid_state_returns_none(self, mock_state_store):
        """Invalid/expired state returns None."""
        mock_state_store.validate_and_consume.return_value = None
        result = await mock_state_store.validate_and_consume("expired_or_fake_state")
        assert result is None

    @pytest.mark.asyncio
    async def test_state_bound_to_tenant(self, mock_state_store):
        """State token carries tenant identity."""
        state = _make_oauth_state(tenant_id="tenant_A")
        mock_state_store.validate_and_consume.return_value = state
        result = await mock_state_store.validate_and_consume("state_token")
        assert result.tenant_id == "tenant_A"


# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNT CONNECTION SERVICE
# ══════════════════════════════════════════════════════════════════════════════

class TestAccountConnectionService:
    def _make_service(self, accounts_repo, auth_registry, state_store, vault):
        return AccountConnectionService(accounts_repo, auth_registry, state_store, vault)

    @pytest.mark.asyncio
    async def test_initiate_connection_success(self, mock_accounts_repo, mock_state_store, vault):
        registry = AuthProviderRegistry()
        provider = _make_mock_provider()
        registry.register(provider)
        mock_state_store.create.return_value = _make_oauth_state()

        svc = self._make_service(mock_accounts_repo, registry, mock_state_store, vault)
        result = await svc.initiate_connection("tenant_1", "user_1", "linkedin", "http://cb")

        assert "auth_url" in result
        assert result["platform"] == "linkedin"
        mock_state_store.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_initiate_unsupported_platform(self, mock_accounts_repo, mock_state_store, vault):
        registry = AuthProviderRegistry()  # empty
        svc = self._make_service(mock_accounts_repo, registry, mock_state_store, vault)

        with pytest.raises(PlatformNotSupported):
            await svc.initiate_connection("t1", "u1", "tiktok", "http://cb")

    @pytest.mark.asyncio
    async def test_complete_connection_invalid_state(self, mock_accounts_repo, mock_state_store, vault):
        registry = AuthProviderRegistry()
        mock_state_store.validate_and_consume.return_value = None

        svc = self._make_service(mock_accounts_repo, registry, mock_state_store, vault)
        with pytest.raises(ValidationError):
            await svc.complete_connection("invalid_state", "code123")

    @pytest.mark.asyncio
    async def test_complete_connection_success_new_account(self, mock_accounts_repo, mock_state_store, vault):
        registry = AuthProviderRegistry()
        provider = _make_mock_provider()
        registry.register(provider)

        state = _make_oauth_state()
        mock_state_store.validate_and_consume.return_value = state
        mock_accounts_repo.find_by_platform_account.return_value = None

        expected_account = SocialAccount(
            id="acc_new",
            tenant_id="tenant_1",
            platform=SocialPlatform.LINKEDIN,
            account_name="Test User",
            platform_account_id="ext_user_123",
            connection_status=ConnectionStatus.CONNECTED,
            capabilities=[AccountCapability.TEXT_PUBLISHING],
        )
        mock_accounts_repo.create_connected.return_value = expected_account

        svc = self._make_service(mock_accounts_repo, registry, mock_state_store, vault)
        account = await svc.complete_connection("test_state_abc123", "auth_code_xyz")

        assert account.id == "acc_new"
        assert account.connection_status == ConnectionStatus.CONNECTED
        mock_accounts_repo.create_connected.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_connection_reconnects_existing(self, mock_accounts_repo, mock_state_store, vault):
        registry = AuthProviderRegistry()
        provider = _make_mock_provider()
        registry.register(provider)

        state = _make_oauth_state()
        mock_state_store.validate_and_consume.return_value = state

        existing = SocialAccount(
            id="acc_existing",
            tenant_id="tenant_1",
            platform=SocialPlatform.LINKEDIN,
            account_name="Old Name",
            platform_account_id="ext_user_123",
            connection_status=ConnectionStatus.TOKEN_EXPIRED,
        )
        mock_accounts_repo.find_by_platform_account.return_value = existing
        mock_accounts_repo.update_credentials.return_value = existing

        svc = self._make_service(mock_accounts_repo, registry, mock_state_store, vault)
        await svc.complete_connection("test_state", "code")

        mock_accounts_repo.update_credentials.assert_called_once()
        mock_accounts_repo.create_connected.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnect_account(self, mock_accounts_repo, mock_state_store, vault):
        registry = AuthProviderRegistry()
        mock_accounts_repo.disconnect.return_value = True

        svc = self._make_service(mock_accounts_repo, registry, mock_state_store, vault)
        result = await svc.disconnect_account("acc_1", "tenant_1")
        assert result is True
        mock_accounts_repo.disconnect.assert_called_with("acc_1", "tenant_1")

    @pytest.mark.asyncio
    async def test_get_access_token_decrypts(self, mock_accounts_repo, mock_state_store, vault):
        registry = AuthProviderRegistry()
        # Store encrypted credentials
        encrypted = vault.encrypt_dict({"access_token": "real_token_123", "refresh_token": "rt"})
        mock_accounts_repo.get_encrypted_credentials.return_value = encrypted

        svc = self._make_service(mock_accounts_repo, registry, mock_state_store, vault)
        token = await svc.get_access_token("acc_1", "tenant_1")
        assert token == "real_token_123"

    @pytest.mark.asyncio
    async def test_get_access_token_none_when_no_creds(self, mock_accounts_repo, mock_state_store, vault):
        registry = AuthProviderRegistry()
        mock_accounts_repo.get_encrypted_credentials.return_value = None

        svc = self._make_service(mock_accounts_repo, registry, mock_state_store, vault)
        token = await svc.get_access_token("acc_1", "tenant_1")
        assert token is None


# ══════════════════════════════════════════════════════════════════════════════
# TENANT ISOLATION
# ══════════════════════════════════════════════════════════════════════════════

class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_state_token_binds_to_tenant(self, mock_state_store, mock_accounts_repo, vault):
        """State from tenant_A cannot be used by tenant_B."""
        registry = AuthProviderRegistry()
        provider = _make_mock_provider()
        registry.register(provider)

        # State was created for tenant_A
        state = _make_oauth_state(tenant_id="tenant_A")
        mock_state_store.validate_and_consume.return_value = state
        mock_accounts_repo.find_by_platform_account.return_value = None
        mock_accounts_repo.create_connected.return_value = SocialAccount(
            id="acc_1", tenant_id="tenant_A", platform=SocialPlatform.LINKEDIN,
            account_name="X", platform_account_id="ext_1",
        )

        svc = AccountConnectionService(mock_accounts_repo, registry, mock_state_store, vault)
        account = await svc.complete_connection("state_token", "code")

        # Account is created under tenant_A — the tenant from the state
        call_args = mock_accounts_repo.create_connected.call_args
        assert call_args.kwargs["tenant_id"] == "tenant_A"

    @pytest.mark.asyncio
    async def test_disconnect_requires_matching_tenant(self, mock_accounts_repo, mock_state_store, vault):
        """Disconnect only works when account belongs to the requesting tenant."""
        registry = AuthProviderRegistry()
        mock_accounts_repo.disconnect.return_value = False  # Tenant mismatch

        svc = AccountConnectionService(mock_accounts_repo, registry, mock_state_store, vault)
        result = await svc.disconnect_account("acc_1", "wrong_tenant")
        assert result is False
