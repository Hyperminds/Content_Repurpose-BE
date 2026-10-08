"""HermesPublisherProvider (Phase 3).

An external-execution PublishingProvider. It owns NO scheduling, queueing,
retry, tenant-resolution, or campaign logic — those belong to Trendzzo. It
receives a normalized PublishInstruction and executes it via a per-platform
workflow driven through the single HermesExecutionAdapter boundary.

Automation policy (Phase 6) is enforced here: if the platform's workflow
declares automation is not allowed — or no workflow is registered — the
provider returns a normalized error and takes NO browser action.
"""

from typing import Callable, Optional

from app.social_publishing.domain.enums import ProviderType
from app.social_publishing.providers.base import PublishingProvider
from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
    ProviderResult,
)
from app.social_publishing.providers.hermes.adapter import HermesExecutionAdapter
from app.social_publishing.providers.hermes.automation_policy import PlatformAutomationPolicy
from app.social_publishing.providers.hermes.workflow import HermesPlatformWorkflow

# Factory that yields a fresh execution adapter per operation (so sessions are
# never shared across jobs/tenants).
AdapterFactory = Callable[[], HermesExecutionAdapter]


class HermesPublisherProvider:
    """Routes a publish to the correct platform workflow via the adapter."""

    def __init__(
        self,
        adapter_factory: AdapterFactory,
        *,
        user_assisted: bool = False,
        automation_policy: Optional[PlatformAutomationPolicy] = None,
    ) -> None:
        self._adapter_factory = adapter_factory
        self._workflows: dict[str, HermesPlatformWorkflow] = {}
        # Central default-deny policy. When none is supplied a fresh empty
        # policy is used → everything denied until explicitly allow-listed.
        self._policy = automation_policy or PlatformAutomationPolicy()
        # A user-assisted Hermes provider is registered under a different
        # ProviderType so routing stays explicit.
        self._provider_type = (
            ProviderType.USER_ASSISTED_AGENT if user_assisted else ProviderType.EXTERNAL_AGENT
        )

    @property
    def automation_policy(self) -> PlatformAutomationPolicy:
        return self._policy

    # ── Workflow registry ─────────────────────────────────────────────────────

    def register_workflow(self, workflow: HermesPlatformWorkflow) -> None:
        self._workflows[workflow.platform] = workflow

    # ── PublishingProvider interface ───────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        from app.social_publishing.domain.enums import ProviderName
        return ProviderName.HERMES.value

    @property
    def provider_type(self) -> ProviderType:
        return self._provider_type

    def capabilities(self, platform: str) -> ProviderCapabilities:
        workflow = self._workflows.get(platform)
        if workflow is None:
            # No workflow → no capabilities (nothing supported).
            return ProviderCapabilities()
        return workflow.capabilities()

    async def publish(
        self, instruction: PublishInstruction, access_token: Optional[str]
    ) -> ProviderResult:
        """
        Execute via the platform workflow, enforcing automation policy first.

        `access_token` is unused by Hermes: browser sessions are resolved out
        of band via a stored session reference scoped to the account.
        """
        workflow = self._workflows.get(instruction.platform)

        # No workflow registered → platform unsupported (no browser action).
        if workflow is None:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_PLATFORM_UNSUPPORTED,
                error_message=f"No Hermes workflow for {instruction.platform}",
                retryable=False,
            )

        # Automation policy gate (Phase 6) — default deny, never bypass.
        # BOTH the central allow-list AND the workflow's own declaration must
        # permit automation (defense in depth). Either one denying blocks it.
        if not self._policy.is_allowed(instruction.platform) or not workflow.automation_allowed:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_AUTOMATION_NOT_PERMITTED,
                error_message="Automation is not permitted for this platform",
                retryable=False,
            )

        adapter = self._adapter_factory()
        try:
            result = await workflow.execute(instruction, adapter)
            return result.sanitized()
        except Exception as e:  # never leak raw browser/adapter internals
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.UNKNOWN,
                error_code=ExternalErrorCode.EXTERNAL_PUBLISH_UNKNOWN,
                error_message=f"Provider execution error: {type(e).__name__}",
                retryable=False,  # UNKNOWN must be verified, not blind-retried
            ).sanitized()
