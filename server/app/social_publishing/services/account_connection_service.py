"""Account connection service — orchestrates the full OAuth connection flow.

Responsibilities:
  1. Initiate: generate state, build auth URL, return to caller
  2. Complete: validate state, exchange code, get profile, detect capabilities,
     encrypt credentials, store account
  3. Disconnect: clear credentials, mark disconnected
  4. Reconnect: re-run OAuth for an existing account (updates credentials)

This service coordinates auth providers, the state store, the credential vault,
and the accounts repository without containing platform-specific logic.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.social_publishing.auth.oauth_state_store import OAuthStateStore
from app.social_publishing.auth.provider import AuthProviderRegistry, PKCEChallenge
from app.social_publishing.credentials.vault import CredentialVault
from app.social_publishing.domain.enums import ConnectionStatus, SocialPlatform
from app.social_publishing.domain.exceptions import (
    PlatformNotSupported,
    SocialPublishingError,
    ValidationError,
)
from app.social_publishing.domain.models import OAuthState, SocialAccount
from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository
from app.services.logger import log


class AccountConnectionService:
    """Orchestrates OAuth account connection flows."""

    def __init__(
        self,
        accounts_repo: SocialAccountsRepository,
        auth_registry: AuthProviderRegistry,
        state_store: OAuthStateStore,
        vault: CredentialVault,
    ) -> None:
        self._accounts = accounts_repo
        self._auth_registry = auth_registry
        self._state_store = state_store
        self._vault = vault

    # ── Step 1: Initiate ──────────────────────────────────────────────────────

    async def initiate_connection(
        self,
        tenant_id: str,
        user_id: str,
        platform: str,
        redirect_uri: str,
    ) -> dict:
        """
        Start the OAuth flow: generate state, return the authorization URL.

        Returns:
            {"auth_url": str, "state": str, "platform": str}
        """
        validated_platform = _validate_platform(platform)

        provider = self._auth_registry.get(validated_platform)
        if not provider:
            raise PlatformNotSupported(platform)

        # Providers requiring OAuth 2.0 PKCE (e.g. X/Twitter) expose a
        # create_pkce_challenge() method that returns a PKCEChallenge. We only
        # treat the flow as PKCE when a genuine PKCEChallenge is returned, so
        # non-PKCE providers (and test mocks) are unaffected.
        pkce = None
        factory = getattr(provider, "create_pkce_challenge", None)
        if callable(factory):
            candidate = factory()
            if isinstance(candidate, PKCEChallenge):
                pkce = candidate

        # Create CSRF-safe state token, persisting the PKCE verifier server-side
        oauth_state = await self._state_store.create(
            tenant_id=tenant_id,
            user_id=user_id,
            platform=validated_platform,
            redirect_uri=redirect_uri,
            code_verifier=pkce.verifier if pkce else "",
        )

        # Build authorization URL with state (and PKCE challenge when applicable)
        if pkce is not None:
            auth_url = provider.get_auth_url(
                state=oauth_state.state_token,
                redirect_uri=redirect_uri,
                code_challenge=pkce.challenge,
            )
        else:
            auth_url = provider.get_auth_url(
                state=oauth_state.state_token,
                redirect_uri=redirect_uri,
            )

        log.info(
            "OAuth flow initiated",
            platform=platform,
            tenant_id=tenant_id,
        )

        return {
            "auth_url": auth_url,
            "state": oauth_state.state_token,
            "platform": platform,
        }

    # ── Step 2: Complete (callback) ───────────────────────────────────────────

    async def complete_connection(
        self,
        state_token: str,
        authorization_code: str,
    ) -> SocialAccount:
        """
        Complete the OAuth flow after the platform redirects back.

        Validates state, exchanges code, retrieves profile, detects capabilities,
        encrypts credentials, and stores the connected account.

        Returns the created/updated SocialAccount.
        """
        # Validate and consume state (CSRF + replay protection)
        oauth_state = await self._state_store.validate_and_consume(state_token)
        if not oauth_state:
            raise ValidationError("Invalid or expired OAuth state — please restart the connection flow")

        provider = self._auth_registry.get(oauth_state.platform)
        if not provider:
            raise PlatformNotSupported(oauth_state.platform.value)

        # Exchange authorization code for tokens. Providers that use PKCE
        # accept a code_verifier kwarg; others ignore it.
        if oauth_state.code_verifier:
            token_response = await provider.exchange_code(
                code=authorization_code,
                redirect_uri=oauth_state.redirect_uri,
                code_verifier=oauth_state.code_verifier,
            )
        else:
            token_response = await provider.exchange_code(
                code=authorization_code,
                redirect_uri=oauth_state.redirect_uri,
            )

        if not token_response.access_token:
            raise SocialPublishingError("OAuth token exchange returned empty access token")

        # Get account information
        account_info = await provider.get_account_info(token_response.access_token)

        # Detect capabilities
        capabilities = await provider.detect_capabilities(
            token_response.access_token, account_info
        )

        # Encrypt credentials before storage
        credentials_to_encrypt = {
            "access_token": token_response.access_token,
            "refresh_token": token_response.refresh_token,
            "token_type": token_response.token_type,
        }
        encrypted_credentials = self._vault.encrypt_dict(credentials_to_encrypt)

        # Calculate token expiry
        token_expires_at = None
        if token_response.expires_in_seconds > 0:
            token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=token_response.expires_in_seconds
            )

        # Check if this platform account is already connected for this tenant
        existing = await self._accounts.find_by_platform_account(
            tenant_id=oauth_state.tenant_id,
            platform=oauth_state.platform,
            platform_account_id=account_info.platform_account_id,
        )

        if existing:
            # Reconnect: update credentials on existing account
            account = await self._accounts.update_credentials(
                account_id=existing.id,
                tenant_id=oauth_state.tenant_id,
                encrypted_credentials=encrypted_credentials,
                token_expires_at=token_expires_at,
                scopes=token_response.scopes,
                connection_status=ConnectionStatus.CONNECTED,
            )
            log.info(
                "Social account reconnected",
                account_id=existing.id,
                platform=oauth_state.platform.value,
                tenant_id=oauth_state.tenant_id,
            )
            return account
        else:
            # New connection
            account = await self._accounts.create_connected(
                tenant_id=oauth_state.tenant_id,
                platform=oauth_state.platform,
                account_name=account_info.account_name,
                platform_account_id=account_info.platform_account_id,
                account_type=account_info.account_type,
                encrypted_credentials=encrypted_credentials,
                token_expires_at=token_expires_at,
                scopes=token_response.scopes,
                capabilities=capabilities,
            )
            log.info(
                "Social account connected",
                account_id=account.id,
                platform=oauth_state.platform.value,
                tenant_id=oauth_state.tenant_id,
                capabilities=[c.value for c in capabilities],
            )
            return account

    # ── Disconnect ────────────────────────────────────────────────────────────

    async def disconnect_account(self, account_id: str, tenant_id: str) -> bool:
        """Disconnect an account — clears credentials, marks disconnected."""
        success = await self._accounts.disconnect(account_id, tenant_id)
        if success:
            log.info("Social account disconnected", account_id=account_id, tenant_id=tenant_id)
        return success

    # ── Token access (for publishing orchestrator) ────────────────────────────

    async def get_access_token(self, account_id: str, tenant_id: str) -> Optional[str]:
        """
        Retrieve decrypted access token for an account.
        Used by the publishing orchestrator — NEVER exposed through API.
        """
        encrypted = await self._accounts.get_encrypted_credentials(account_id, tenant_id)
        if not encrypted:
            return None

        decrypted = self._vault.decrypt_dict(encrypted)
        return decrypted.get("access_token") or None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_platform(platform: str) -> SocialPlatform:
    try:
        return SocialPlatform(platform.lower().strip())
    except ValueError:
        raise PlatformNotSupported(platform)
