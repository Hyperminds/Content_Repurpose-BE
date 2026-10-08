"""Provider router (Phase 16).

Resolves the correct PublishingProvider for a connected account based on its
provider_type — NOT via scattered if/else platform checks.

Routing rule:
    provider = router.resolve(account)

  - NATIVE_API           → the registered native provider (wraps existing
                           platform publishers: LinkedIn/Meta/X)
  - EXTERNAL_AGENT       → the Hermes provider (only if registered/enabled)
  - USER_ASSISTED_AGENT  → the Hermes provider (user-assisted mode)
  - BYOK_API             → a BYOK provider for the account's provider_name

If no provider is registered for a required type, resolution returns None and
the engine reports EXTERNAL_PLATFORM_UNSUPPORTED — it never silently falls back
to a different execution method.
"""

from typing import Optional

from app.social_publishing.domain.enums import ProviderType
from app.social_publishing.domain.models import SocialAccount
from app.social_publishing.providers.base import PublishingProvider


class ProviderRouter:
    """Maps a connected account to the provider that will execute its posts."""

    def __init__(self) -> None:
        # Providers registered by execution type. BYOK is keyed by provider_name
        # since multiple BYOK providers may coexist (e.g. byok_x).
        self._by_type: dict[ProviderType, PublishingProvider] = {}
        self._byok_by_name: dict[str, PublishingProvider] = {}

    # ── Registration ────────────────────────────────────────────────────────

    def register(self, provider: PublishingProvider) -> None:
        """Register a provider for its provider_type."""
        if provider.provider_type == ProviderType.BYOK_API:
            self._byok_by_name[provider.provider_name] = provider
        else:
            self._by_type[provider.provider_type] = provider

    def has(self, provider_type: ProviderType) -> bool:
        if provider_type == ProviderType.BYOK_API:
            return bool(self._byok_by_name)
        return provider_type in self._by_type

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve(self, account: SocialAccount) -> Optional[PublishingProvider]:
        """
        Return the provider that should execute this account's posts, or None
        if no provider is registered for the account's provider_type.

        The engine treats None as "unsupported" — it must not fall back to
        another execution method.
        """
        if account.provider_type == ProviderType.BYOK_API:
            return self._byok_by_name.get(account.provider_name)
        return self._by_type.get(account.provider_type)

    @property
    def registered_types(self) -> list[str]:
        types = [t.value for t in self._by_type]
        if self._byok_by_name:
            types.append(ProviderType.BYOK_API.value)
        return types
