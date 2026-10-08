"""The PublishingProvider abstraction (Phase 15/16).

A PublishingProvider is the single interface the publishing engine uses to
execute a job, regardless of HOW execution happens (native API, Hermes browser
agent, user-assisted, BYOK). It sits above the existing platform publishers.

Contract:
  - `provider_name` — stable identifier (matches SocialAccount.provider_name)
  - `provider_type` — NATIVE_API / EXTERNAL_AGENT / USER_ASSISTED_AGENT / BYOK_API
  - `capabilities()` — ProviderCapabilities for a given platform
  - `publish(instruction, access_token)` — returns a normalized ProviderResult

Providers own ONLY execution. They must not schedule, queue, retry, resolve
tenants, or manage campaigns — that is Trendzzo's job.
"""

from typing import Optional, Protocol, runtime_checkable

from app.social_publishing.domain.enums import ProviderType
from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import PublishInstruction, ProviderResult


@runtime_checkable
class PublishingProvider(Protocol):
    """Protocol every publishing provider satisfies."""

    @property
    def provider_name(self) -> str:
        """Stable provider identifier (matches SocialAccount.provider_name)."""
        ...

    @property
    def provider_type(self) -> ProviderType:
        """The execution method this provider uses."""
        ...

    def capabilities(self, platform: str) -> ProviderCapabilities:
        """What this provider can do for the given platform."""
        ...

    async def publish(
        self, instruction: PublishInstruction, access_token: Optional[str]
    ) -> ProviderResult:
        """
        Execute a single publish and return a normalized result.

        `access_token` is provided for native/BYOK API providers. Agent
        providers that rely on a stored session reference may ignore it and
        resolve their own session out of band (scoped to the account).
        """
        ...
