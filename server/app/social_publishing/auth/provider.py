"""OAuth provider protocol and registry.

Each social platform implements `SocialAuthProvider` to handle its specific
OAuth flow. The registry maps platform names to provider instances, decoupling
the connection service from any platform-specific code.
"""

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from app.social_publishing.domain.enums import AccountCapability, SocialPlatform


@dataclass
class OAuthTokenResponse:
    """Standardized token response from a provider's code exchange."""

    access_token: str
    refresh_token: str = ""
    expires_in_seconds: int = 0
    token_type: str = "Bearer"
    scopes: list[str] = field(default_factory=list)


@dataclass
class AccountInfo:
    """Standardized account information returned after OAuth completes."""

    platform_account_id: str
    account_name: str
    account_type: str = "personal"  # personal, business, page, organization
    email: str = ""
    profile_url: str = ""
    avatar_url: str = ""


@runtime_checkable
class SocialAuthProvider(Protocol):
    """Protocol that all platform auth providers must satisfy."""

    @property
    def platform(self) -> SocialPlatform:
        """The platform this provider handles."""
        ...

    @property
    def required_scopes(self) -> list[str]:
        """OAuth scopes needed for publishing functionality."""
        ...

    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """
        Build the OAuth authorization URL for user redirect.

        Args:
            state: CSRF-safe state token (validated on callback).
            redirect_uri: Where the platform should redirect after authorization.

        Returns:
            The full authorization URL to redirect the user to.
        """
        ...

    async def exchange_code(
        self, code: str, redirect_uri: str
    ) -> OAuthTokenResponse:
        """
        Exchange an authorization code for tokens.

        Args:
            code: The authorization code from the OAuth callback.
            redirect_uri: The same redirect URI used in the auth request.

        Returns:
            Standardized token response.

        Raises:
            SocialPublishingError on failure.
        """
        ...

    async def get_account_info(self, access_token: str) -> AccountInfo:
        """
        Retrieve the connected account's profile information.

        Args:
            access_token: A valid access token.

        Returns:
            Standardized account information.
        """
        ...

    async def detect_capabilities(
        self, access_token: str, account_info: AccountInfo
    ) -> list[AccountCapability]:
        """
        Determine what the connected account can do on this platform.

        Args:
            access_token: A valid access token.
            account_info: Previously retrieved account info.

        Returns:
            List of capabilities this account supports.
        """
        ...

    async def refresh_token(self, refresh_token: str) -> Optional[OAuthTokenResponse]:
        """
        Refresh an expired access token.

        Args:
            refresh_token: The stored refresh token.

        Returns:
            New token response, or None if refresh is not supported/fails.
        """
        ...


@dataclass
class PKCEChallenge:
    """A PKCE verifier/challenge pair for OAuth 2.0 PKCE flows."""

    verifier: str
    challenge: str
    method: str = "S256"


class AuthProviderRegistry:
    """Registry mapping platforms to their auth provider implementations."""

    def __init__(self) -> None:
        self._providers: dict[str, SocialAuthProvider] = {}

    def register(self, provider: SocialAuthProvider) -> None:
        """Register an auth provider."""
        self._providers[provider.platform.value] = provider

    def get(self, platform: str | SocialPlatform) -> Optional[SocialAuthProvider]:
        """Get the auth provider for a platform, or None if not registered."""
        key = platform.value if isinstance(platform, SocialPlatform) else platform.lower()
        return self._providers.get(key)

    def has(self, platform: str | SocialPlatform) -> bool:
        """Check if a provider is registered."""
        key = platform.value if isinstance(platform, SocialPlatform) else platform.lower()
        return key in self._providers

    @property
    def supported_platforms(self) -> list[str]:
        """List platforms that have OAuth providers registered."""
        return list(self._providers.keys())
