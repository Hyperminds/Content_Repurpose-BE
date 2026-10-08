"""FakeHermesWorkflow — a non-browser workflow for tests and wiring validation.

It exercises the full workflow contract (connect → publish → verify-on-unknown
→ close) using an injected execution adapter, and maps low-level adapter
outcomes to normalized ProviderResults. It performs NO real browser actions.

`automation_allowed` is a constructor flag so tests can assert the
policy-denied path (EXTERNAL_AUTOMATION_NOT_PERMITTED) without a real platform.
"""

from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
    ProviderResult,
)
from app.social_publishing.providers.hermes.adapter import (
    AdapterOutcome,
    FakeOutcome,
    HermesExecutionAdapter,
)

# Fake platform identifier used only in tests/dev.
FAKE_PLATFORM = "fake_platform"


class FakeHermesWorkflow:
    """A test workflow that satisfies the HermesPlatformWorkflow contract."""

    def __init__(
        self,
        platform: str = FAKE_PLATFORM,
        automation_allowed: bool = True,
        requires_user_action: bool = False,
    ) -> None:
        self._platform = platform
        self._automation_allowed = automation_allowed
        self._requires_user_action = requires_user_action

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def automation_allowed(self) -> bool:
        return self._automation_allowed

    @property
    def requires_user_action(self) -> bool:
        return self._requires_user_action

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            text=True, image=True, links=True, scheduling=False,
            browser_automation=True,
            user_confirmation=self._requires_user_action,
        )

    async def execute(
        self, instruction: PublishInstruction, adapter: HermesExecutionAdapter
    ) -> ProviderResult:
        """Drive the adapter and normalize its outcome."""
        try:
            await adapter.connect(instruction)
            outcome = await adapter.publish(instruction)

            # UNKNOWN → verify before deciding (never blind-retry).
            if outcome.kind == FakeOutcome.UNKNOWN.value:
                verified = await adapter.verify(instruction)
                return _from_outcome(verified, treat_unknown_as_unknown=True)

            return _from_outcome(outcome, treat_unknown_as_unknown=True)
        finally:
            await adapter.close()


def _from_outcome(
    outcome: AdapterOutcome, treat_unknown_as_unknown: bool
) -> ProviderResult:
    """Map a low-level AdapterOutcome to a normalized ProviderResult."""
    kind = outcome.kind

    if kind == FakeOutcome.SUCCESS.value:
        return ProviderResult(
            success=True,
            status=ExternalResultStatus.PUBLISHED,
            external_post_id=outcome.external_post_id,
            external_url=outcome.external_url,
        )

    if kind == FakeOutcome.ACTION_REQUIRED.value:
        return ProviderResult(
            success=False,
            status=ExternalResultStatus.ACTION_REQUIRED,
            error_code=ExternalErrorCode.ACTION_REQUIRED,
            error_message="User action required to continue publishing",
            retryable=False,
        )

    if kind == FakeOutcome.AUTH_REQUIRED.value:
        return ProviderResult(
            success=False,
            status=ExternalResultStatus.FAILED,
            error_code=ExternalErrorCode.EXTERNAL_AUTH_REQUIRED,
            error_message="Account requires re-authentication",
            retryable=False,
        )

    if kind == FakeOutcome.TIMEOUT.value:
        return ProviderResult(
            success=False,
            status=ExternalResultStatus.FAILED,
            error_code=ExternalErrorCode.EXTERNAL_PUBLISH_TIMEOUT,
            error_message="Publishing timed out",
            retryable=True,
        )

    if kind == FakeOutcome.VERIFICATION_FAILED.value:
        return ProviderResult(
            success=False,
            status=ExternalResultStatus.FAILED,
            error_code=ExternalErrorCode.EXTERNAL_VERIFICATION_FAILED,
            error_message="Could not verify the publish result",
            retryable=False,
        )

    if kind == FakeOutcome.UNKNOWN.value:
        # Reached only if verification itself returned UNKNOWN.
        return ProviderResult(
            success=False,
            status=ExternalResultStatus.UNKNOWN,
            error_code=ExternalErrorCode.EXTERNAL_PUBLISH_UNKNOWN,
            error_message="Publish outcome is uncertain",
            retryable=False,  # engine must verify, never blind-retry
        )

    # Default: FAILURE
    return ProviderResult(
        success=False,
        status=ExternalResultStatus.FAILED,
        error_code=ExternalErrorCode.EXTERNAL_PROVIDER_UNAVAILABLE,
        error_message="Publishing failed",
        retryable=True,
    )
