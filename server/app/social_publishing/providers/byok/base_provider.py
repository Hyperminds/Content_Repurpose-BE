"""Base BYOK publishing provider abstraction (Phase 20).

A BYOK provider is a PublishingProvider (provider_type = BYOK_API) that uses a
customer's OWN platform API credentials, resolved from the tenant+account-scoped
ByokCredentialStore, to call the platform's official API.

This base class supplies the shared plumbing (identity, credential resolution,
capability declaration) and leaves the actual platform call abstract. It does
NOT implement any concrete platform flow — that is future, flag-gated work.

Security invariants inherited by all BYOK providers:
  - credentials resolved per tenant+account only (never cross-tenant)
  - decrypted credentials are used transiently and never returned/logged
  - uses the official platform API; never a bypass of pricing/limits/policy
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.social_publishing.domain.enums import ProviderType
from app.social_publishing.providers.byok.credential_store import ByokCredentialStore
from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
    ProviderResult,
)


class BaseByokProvider(ABC):
    """Shared base for customer-credential (BYOK) providers."""

    def __init__(
        self,
        provider_name: str,
        credential_store: Optional[ByokCredentialStore] = None,
    ) -> None:
        self._provider_name = provider_name
        self._store = credential_store or ByokCredentialStore()

    # ── PublishingProvider identity ─────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.BYOK_API

    @abstractmethod
    def capabilities(self, platform: str) -> ProviderCapabilities:
        """Concrete providers declare what the platform's API supports."""
        ...

    # ── Publish ─────────────────────────────────────────────────────────────────

    async def publish(
        self, instruction: PublishInstruction, access_token: Optional[str]
    ) -> ProviderResult:
        """
        Resolve the customer's credentials and delegate to the platform call.

        `access_token` is ignored: BYOK providers use the customer's own stored
        credentials, not a Trendzzo-managed OAuth token.
        """
        creds = await self._store.get_decrypted(
            tenant_id=instruction.tenant_id,
            account_id=instruction.meta.get("account_record_id", ""),
            provider_name=self._provider_name,
        )
        if not creds:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_AUTH_REQUIRED,
                error_message="No BYOK credentials configured for this account",
                retryable=False,
            )
        # Concrete providers implement the official-API call. Not implemented
        # here — this is the abstraction only.
        return await self._publish_with_credentials(instruction, creds)

    @abstractmethod
    async def _publish_with_credentials(
        self, instruction: PublishInstruction, credentials: dict
    ) -> ProviderResult:
        """Call the platform's official API using the customer's credentials."""
        ...
