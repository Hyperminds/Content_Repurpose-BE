"""Hermes platform-workflow plugins (Phase 5 + 6).

A platform workflow encapsulates the per-platform steps Hermes performs
(connect, compose, media, publish, verify) so the Hermes provider never grows
a giant `if platform == ...` block. Each real platform would ship its own
workflow subclass — but NONE are implemented here yet by design.

Every workflow MUST declare `automation_allowed`. When False, the provider
returns EXTERNAL_AUTOMATION_NOT_PERMITTED and no browser action is taken.
Workflows must never contain CAPTCHA/MFA/anti-bot/stealth/evasion logic.

Only a FakeHermesWorkflow is provided, for tests and wiring validation.
"""

from typing import Protocol, runtime_checkable

from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import PublishInstruction, ProviderResult


@runtime_checkable
class HermesPlatformWorkflow(Protocol):
    """Contract for a per-platform Hermes workflow."""

    @property
    def platform(self) -> str:
        """Platform this workflow handles (SocialPlatform value)."""
        ...

    @property
    def automation_allowed(self) -> bool:
        """
        Whether browser automation is permitted for this platform.

        MUST be explicitly set. Defaults for real workflows should be False
        until a compliance review approves the platform.
        """
        ...

    @property
    def requires_user_action(self) -> bool:
        """Whether this workflow may need manual user steps (login/MFA/confirm)."""
        ...

    def capabilities(self) -> ProviderCapabilities:
        """What this workflow can publish."""
        ...

    async def execute(
        self, instruction: PublishInstruction, adapter: "object"
    ) -> ProviderResult:
        """
        Drive the workflow using the given execution adapter.

        The workflow orchestrates adapter calls (connect/compose/media/publish/
        verify) and returns a normalized ProviderResult. It must NOT talk to a
        browser directly — all runtime interaction goes through the adapter.
        """
        ...
