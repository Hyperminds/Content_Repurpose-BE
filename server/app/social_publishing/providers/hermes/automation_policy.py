"""Platform automation policy (Phase 6).

Central, DEFAULT-DENY registry that decides whether browser automation is
permitted for a given platform. A platform is automatable ONLY if it has been
explicitly allow-listed here (and its workflow also declares it). This makes
enabling automation an explicit, auditable configuration decision — never an
accident of code.

Absolutely NO bypass mechanisms live here or anywhere downstream:
  - no CAPTCHA solving / bypass
  - no MFA bypass
  - no anti-bot / fingerprint / stealth evasion
  - no proxy rotation for evasion
  - no rate-limit / API-restriction / payment bypass
  - no security-control circumvention

If a platform is not permitted, callers must return
EXTERNAL_AUTOMATION_NOT_PERMITTED and take no browser action.
"""

from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalProviderError,
)


class PlatformAutomationPolicy:
    """Default-deny allow-list of platforms permitted for browser automation."""

    def __init__(self) -> None:
        # Empty by default → EVERYTHING is denied until explicitly allowed.
        self._allowed: set[str] = set()

    def allow(self, platform: str) -> None:
        """
        Explicitly permit automation for a platform.

        Should only be called after a documented compliance review confirms the
        platform's terms permit this automation. Ships called for nothing.
        """
        self._allowed.add(platform)

    def deny(self, platform: str) -> None:
        """Revoke a previously granted permission."""
        self._allowed.discard(platform)

    def is_allowed(self, platform: str) -> bool:
        """True only if the platform has been explicitly allow-listed."""
        return platform in self._allowed

    def ensure_allowed(self, platform: str) -> None:
        """
        Raise EXTERNAL_AUTOMATION_NOT_PERMITTED if the platform is not allowed.

        Use at the top of any automation entry point as a hard gate.
        """
        if not self.is_allowed(platform):
            raise ExternalProviderError(
                code=ExternalErrorCode.EXTERNAL_AUTOMATION_NOT_PERMITTED,
                message=f"Automation is not permitted for platform '{platform}'",
                retryable=False,
            )

    @property
    def allowed_platforms(self) -> list[str]:
        return sorted(self._allowed)
