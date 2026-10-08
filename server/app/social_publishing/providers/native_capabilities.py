"""Declared capabilities for the built-in native-API providers.

These describe what each native integration (LinkedIn, Meta, X) can publish.
They are data-only declarations consumed by the engine's pre-flight capability
check. Native providers never use browser automation or user confirmation.

Kept separate from the integrations themselves so this layer has no import
dependency on integration internals.
"""

from app.social_publishing.domain.enums import ProviderName
from app.social_publishing.providers.capabilities import ProviderCapabilities


# Capability profiles keyed by provider name (ProviderName.value).
NATIVE_CAPABILITIES: dict[str, ProviderCapabilities] = {
    ProviderName.LINKEDIN.value: ProviderCapabilities(
        text=True, image=True, video=True, links=True,
        scheduling=True, analytics=False,
        browser_automation=False, user_confirmation=False,
    ),
    ProviderName.META.value: ProviderCapabilities(
        text=True, image=True, video=True, links=True,
        scheduling=True, analytics=True,
        browser_automation=False, user_confirmation=False,
    ),
    ProviderName.X.value: ProviderCapabilities(
        text=True, image=True, video=True, links=True,
        scheduling=True, analytics=False,
        browser_automation=False, user_confirmation=False,
    ),
}


def native_capabilities_for(provider_name: str) -> ProviderCapabilities:
    """
    Capabilities for a native provider name.

    Falls back to a conservative text-only profile for unknown providers so a
    missing entry never accidentally grants more than it should.
    """
    return NATIVE_CAPABILITIES.get(
        provider_name,
        ProviderCapabilities(text=True, links=True, scheduling=True),
    )
