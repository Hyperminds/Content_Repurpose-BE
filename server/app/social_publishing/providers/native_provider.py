"""Native-API publishing provider (Phase 16 / 17 / 18 / 19).

Wraps the existing platform publishers (LinkedIn, Meta, X) — resolved from the
existing PublisherRegistry — so they satisfy the new PublishingProvider
interface WITHOUT any change to the integrations themselves.

This is the adapter that keeps the native path behaving exactly as before:
LinkedIn still publishes via the LinkedIn API, Meta via the Graph API, X via
the X API. The engine just reaches them through the provider abstraction now.
"""

from typing import Optional

from app.social_publishing.domain.enums import PostStatus, ProviderType
from app.social_publishing.domain.models import SocialPost
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.providers.base import PublishingProvider
from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
    ProviderResult,
)
from app.social_publishing.providers.native_capabilities import native_capabilities_for


class NativeApiProvider:
    """
    Adapts the platform-keyed PublisherRegistry to the PublishingProvider API.

    One NativeApiProvider instance fronts all native integrations. It looks up
    the correct platform publisher per instruction and translates the legacy
    PublishingResult into a normalized ProviderResult.
    """

    def __init__(self, publisher_registry: PublisherRegistry) -> None:
        self._registry = publisher_registry

    @property
    def provider_name(self) -> str:
        # NativeApiProvider fronts multiple platform providers; the concrete
        # provider name lives on the account. This label is for routing/logs.
        return "native_api"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.NATIVE_API

    def capabilities(self, platform: str) -> ProviderCapabilities:
        # Native capability profile is keyed by the account's provider_name,
        # but platform is a safe proxy here since the engine passes the
        # account's provider_name through the instruction. We expose per the
        # provider name mapping used by the account.
        return native_capabilities_for(platform)

    async def publish(
        self, instruction: PublishInstruction, access_token: Optional[str]
    ) -> ProviderResult:
        """Delegate to the existing platform publisher for this platform."""
        publisher = self._registry.get_optional(instruction.platform)
        if publisher is None:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_PLATFORM_UNSUPPORTED,
                error_message=f"No native publisher for {instruction.platform}",
                retryable=False,
            )

        if not access_token:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_AUTH_REQUIRED,
                error_message="Credentials unavailable",
                retryable=False,
            )

        # Build the SocialPost the legacy publisher expects. The worker already
        # sets account_id to the platform_account_id before calling us, so we
        # pass the instruction's account_id straight through.
        post = SocialPost(
            id=instruction.operation_id,
            tenant_id=instruction.tenant_id,
            account_id=instruction.account_id,
            platform=_platform_enum(instruction.platform),
            status=PostStatus.PUBLISHING,
            content=instruction.text,
            media_urls=list(instruction.media_urls),
        )

        result = await publisher.publish(post, access_token)

        if result.success:
            return ProviderResult(
                success=True,
                status=ExternalResultStatus.PUBLISHED,
                external_post_id=result.platform_post_id,
            )

        return ProviderResult(
            success=False,
            status=ExternalResultStatus.FAILED,
            error_code=ExternalErrorCode.EXTERNAL_CONTENT_REJECTED,
            error_message=result.error_message,
            retryable=result.retryable,
        ).sanitized()


def _platform_enum(platform: str):
    from app.social_publishing.domain.enums import SocialPlatform
    return SocialPlatform(platform)
