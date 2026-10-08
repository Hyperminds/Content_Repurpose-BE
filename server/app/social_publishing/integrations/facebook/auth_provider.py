"""Facebook OAuth auth provider — Facebook Login for Page publishing.

Implements the SocialAuthProvider protocol. Key design:
  - Uses Facebook Login (OAuth 2.0) to get a User access token
  - Exchanges for long-lived User token (60 days)
  - Retrieves Page access tokens via /{user_id}/accounts
  - Detects CREATE_CONTENT task on Pages for publishing eligibility
  - Personal profile publishing is NOT supported (clearly communicated)

The Page access token (derived from long-lived user token) does not expire
and is what gets stored for publishing. The user token itself is transient.
"""

import os
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.social_publishing.domain.enums import AccountCapability, SocialPlatform
from app.social_publishing.domain.exceptions import SocialPublishingError
from app.social_publishing.auth.provider import AccountInfo, OAuthTokenResponse
from app.social_publishing.integrations.facebook.constants import (
    AUTHORIZATION_URL,
    GRAPH_API_BASE,
    HTTP_TIMEOUT,
    PUBLISHING_TASK,
    REQUIRED_SCOPES,
    TOKEN_URL,
)


class FacebookAuthProvider:
    """Facebook OAuth 2.0 provider for Page publishing."""

    def __init__(self) -> None:
        self._app_id = os.getenv("FACEBOOK_APP_ID", "")
        self._app_secret = os.getenv("FACEBOOK_APP_SECRET", "")

    @property
    def platform(self) -> SocialPlatform:
        return SocialPlatform.META

    @property
    def required_scopes(self) -> list[str]:
        return list(REQUIRED_SCOPES)

    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """Build the Facebook OAuth authorization URL."""
        if not self._app_id:
            raise SocialPublishingError("Facebook OAuth not configured (missing FACEBOOK_APP_ID)")

        params = {
            "client_id": self._app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(REQUIRED_SCOPES),
            "state": state,
        }
        return f"{AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokenResponse:
        """
        Exchange authorization code for tokens.

        Flow: code → short-lived user token → long-lived user token → Page tokens.
        Returns the Page access token (non-expiring) for the first eligible Page.
        """
        if not self._app_id or not self._app_secret:
            raise SocialPublishingError("Facebook OAuth credentials not configured")

        # Step 1: Exchange code for short-lived user token
        short_lived_token = await self._exchange_code_for_token(code, redirect_uri)

        # Step 2: Exchange for long-lived user token
        long_lived_token = await self._get_long_lived_token(short_lived_token)
        user_token = long_lived_token or short_lived_token

        # Step 3: Get Page tokens (these are what we actually store)
        pages = await self._get_user_pages(user_token)

        if not pages:
            # User has no Pages — return user token with limited capability
            return OAuthTokenResponse(
                access_token=user_token,
                refresh_token="",
                expires_in_seconds=5_184_000,  # 60 days
                token_type="bearer",
                scopes=REQUIRED_SCOPES,
            )

        # Return the first Page's access token (Page tokens from long-lived
        # user tokens do not expire)
        first_page = pages[0]
        return OAuthTokenResponse(
            access_token=first_page["access_token"],
            refresh_token=user_token,  # Store user token as "refresh" for re-fetching page tokens
            expires_in_seconds=0,  # Non-expiring Page token
            token_type="bearer",
            scopes=REQUIRED_SCOPES,
        )

    async def get_account_info(self, access_token: str) -> AccountInfo:
        """Retrieve account info — checks if this is a Page token or User token."""
        # Try to get Page info first (if access_token is a Page token)
        page_info = await self._get_page_info(access_token)
        if page_info:
            return page_info

        # Fallback: get user info
        return await self._get_user_info(access_token)

    async def detect_capabilities(
        self, access_token: str, account_info: AccountInfo
    ) -> list[AccountCapability]:
        """
        Detect publishing capabilities.

        Only grants publishing capabilities if the account is a Page with
        CREATE_CONTENT task. Personal profiles get NO publishing capability.
        """
        capabilities: list[AccountCapability] = []

        if account_info.account_type == "page":
            capabilities.append(AccountCapability.TEXT_PUBLISHING)
            capabilities.append(AccountCapability.IMAGE_PUBLISHING)
            capabilities.append(AccountCapability.VIDEO_PUBLISHING)
            capabilities.append(AccountCapability.SCHEDULED_PUBLISHING)
            capabilities.append(AccountCapability.ANALYTICS)
        # Personal accounts: no publishing capability granted
        # User sees: "Publishing requires a Facebook Page"

        return capabilities

    async def refresh_token(self, refresh_token: str) -> Optional[OAuthTokenResponse]:
        """
        Refresh by re-fetching Page tokens from the stored user token.

        The refresh_token field stores the long-lived User access token.
        Page tokens derived from it are non-expiring, but if the user token
        expires, we need re-authentication.
        """
        if not refresh_token:
            return None

        # Verify the user token is still valid
        pages = await self._get_user_pages(refresh_token)
        if not pages:
            return None

        first_page = pages[0]
        return OAuthTokenResponse(
            access_token=first_page["access_token"],
            refresh_token=refresh_token,
            expires_in_seconds=0,
            token_type="bearer",
            scopes=REQUIRED_SCOPES,
        )

    # ── Internal methods ──────────────────────────────────────────────────────

    async def _exchange_code_for_token(self, code: str, redirect_uri: str) -> str:
        """Exchange authorization code for a short-lived User access token."""
        params = {
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(TOKEN_URL, params=params)

        if response.status_code != 200:
            raise SocialPublishingError(
                f"Facebook token exchange failed (HTTP {response.status_code})"
            )

        data = response.json()
        return data.get("access_token", "")

    async def _get_long_lived_token(self, short_lived_token: str) -> Optional[str]:
        """Exchange a short-lived token for a long-lived one (60 days)."""
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "fb_exchange_token": short_lived_token,
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(TOKEN_URL, params=params)

        if response.status_code != 200:
            return None

        data = response.json()
        return data.get("access_token")

    async def _get_user_pages(self, user_token: str) -> list[dict]:
        """
        Retrieve Pages the user manages with CREATE_CONTENT task.

        Returns list of {id, name, access_token, tasks} for eligible Pages.
        """
        url = f"{GRAPH_API_BASE}/me/accounts"
        params = {"access_token": user_token, "fields": "id,name,access_token,tasks"}

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url, params=params)

        if response.status_code != 200:
            return []

        data = response.json()
        pages = data.get("data", [])

        # Filter to only Pages where user can create content
        eligible = []
        for page in pages:
            tasks = page.get("tasks", [])
            if PUBLISHING_TASK in tasks:
                eligible.append(page)

        return eligible

    async def _get_page_info(self, access_token: str) -> Optional[AccountInfo]:
        """Try to get Page info using the token (works if it's a Page token)."""
        url = f"{GRAPH_API_BASE}/me"
        params = {"access_token": access_token, "fields": "id,name,category,link,picture"}

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.get(url, params=params)

            if response.status_code != 200:
                return None

            data = response.json()
            # Pages have a 'category' field; users typically don't
            if "category" in data:
                return AccountInfo(
                    platform_account_id=data.get("id", ""),
                    account_name=data.get("name", "Facebook Page"),
                    account_type="page",
                    email="",
                    profile_url=data.get("link", ""),
                    avatar_url=data.get("picture", {}).get("data", {}).get("url", "")
                    if isinstance(data.get("picture"), dict) else "",
                )
        except Exception:
            pass
        return None

    async def _get_user_info(self, access_token: str) -> AccountInfo:
        """Get Facebook user profile info (fallback when no Page detected)."""
        url = f"{GRAPH_API_BASE}/me"
        params = {"access_token": access_token, "fields": "id,name,email,picture"}

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url, params=params)

        if response.status_code != 200:
            raise SocialPublishingError(
                f"Facebook profile fetch failed (HTTP {response.status_code})"
            )

        data = response.json()
        return AccountInfo(
            platform_account_id=data.get("id", ""),
            account_name=data.get("name", "Facebook User"),
            account_type="personal",  # No publishing capability for personal
            email=data.get("email", ""),
            profile_url=f"https://www.facebook.com/{data.get('id', '')}",
            avatar_url=data.get("picture", {}).get("data", {}).get("url", "")
            if isinstance(data.get("picture"), dict) else "",
        )
