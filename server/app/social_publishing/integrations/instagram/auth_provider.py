"""Instagram OAuth auth provider — Business Login for Instagram flow.

Implements the SocialAuthProvider protocol using the Instagram API with
Instagram Login (direct Instagram OAuth, no Facebook Page required).

OAuth endpoints:
  - Authorization: https://www.instagram.com/oauth/authorize
  - Token exchange: https://api.instagram.com/oauth/access_token
  - Long-lived token: https://graph.instagram.com/access_token
  - Token refresh: https://graph.instagram.com/refresh_access_token
"""

import os
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.social_publishing.domain.enums import AccountCapability, SocialPlatform
from app.social_publishing.domain.exceptions import SocialPublishingError
from app.social_publishing.auth.provider import AccountInfo, OAuthTokenResponse
from app.social_publishing.integrations.instagram.constants import (
    API_VERSION,
    AUTHORIZATION_URL,
    GRAPH_API_HOST,
    HTTP_TIMEOUT,
    LONG_LIVED_TOKEN_URL,
    LONG_LIVED_TOKEN_SECONDS,
    REQUIRED_SCOPES,
    TOKEN_EXCHANGE_URL,
    TOKEN_REFRESH_URL,
)


class InstagramAuthProvider:
    """Instagram Business Login OAuth 2.0 provider."""

    def __init__(self) -> None:
        self._app_id = os.getenv("INSTAGRAM_APP_ID", "")
        self._app_secret = os.getenv("INSTAGRAM_APP_SECRET", "")

    @property
    def platform(self) -> SocialPlatform:
        return SocialPlatform.INSTAGRAM

    @property
    def required_scopes(self) -> list[str]:
        return list(REQUIRED_SCOPES)

    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """Build the Instagram authorization URL."""
        if not self._app_id:
            raise SocialPublishingError("Instagram OAuth not configured (missing INSTAGRAM_APP_ID)")

        params = {
            "client_id": self._app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(REQUIRED_SCOPES),
            "state": state,
        }
        return f"{AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokenResponse:
        """Exchange authorization code for tokens (short-lived then long-lived)."""
        if not self._app_id or not self._app_secret:
            raise SocialPublishingError("Instagram OAuth credentials not configured")

        # Step 1: Exchange code for short-lived token
        short_lived = await self._exchange_for_short_lived(code, redirect_uri)

        # Step 2: Exchange short-lived for long-lived token
        long_lived = await self._exchange_for_long_lived(short_lived.access_token)

        return long_lived

    async def get_account_info(self, access_token: str) -> AccountInfo:
        """Retrieve the Instagram account profile information."""
        url = f"{GRAPH_API_HOST}/{API_VERSION}/me"
        params = {
            "fields": "user_id,username,name,account_type,profile_picture_url",
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                url, params=params, headers={"Authorization": f"Bearer {access_token}"}
            )

        if response.status_code != 200:
            raise SocialPublishingError(
                f"Failed to retrieve Instagram account info (HTTP {response.status_code})"
            )

        data = response.json()
        account_type = data.get("account_type", "").lower()

        return AccountInfo(
            platform_account_id=str(data.get("user_id", data.get("id", ""))),
            account_name=data.get("username", data.get("name", "Instagram User")),
            account_type=_map_account_type(account_type),
            email="",  # Instagram API does not expose email
            profile_url=f"https://www.instagram.com/{data.get('username', '')}",
            avatar_url=data.get("profile_picture_url", ""),
        )

    async def detect_capabilities(
        self, access_token: str, account_info: AccountInfo
    ) -> list[AccountCapability]:
        """Determine publishing capabilities based on account type."""
        capabilities: list[AccountCapability] = []

        # Only professional accounts (business/creator) can publish via API
        if account_info.account_type in ("business", "creator"):
            capabilities.append(AccountCapability.IMAGE_PUBLISHING)
            capabilities.append(AccountCapability.VIDEO_PUBLISHING)
            capabilities.append(AccountCapability.CAROUSEL_PUBLISHING)
            capabilities.append(AccountCapability.STORY_PUBLISHING)
            capabilities.append(AccountCapability.ANALYTICS)
        # Personal accounts have no publishing capability
        # — Trendzzo must NOT claim they can publish

        return capabilities

    async def refresh_token(self, refresh_token: str) -> Optional[OAuthTokenResponse]:
        """
        Refresh a long-lived Instagram token.

        Instagram's refresh uses the access token itself (not a separate refresh token).
        The token passed here is the current long-lived access token.
        """
        if not refresh_token:
            return None

        params = {
            "grant_type": "ig_refresh_token",
            "access_token": refresh_token,
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(TOKEN_REFRESH_URL, params=params)

        if response.status_code != 200:
            return None

        data = response.json()
        return OAuthTokenResponse(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("access_token", ""),  # Instagram reuses access token for refresh
            expires_in_seconds=int(data.get("expires_in", LONG_LIVED_TOKEN_SECONDS)),
            token_type=data.get("token_type", "bearer"),
            scopes=REQUIRED_SCOPES,
        )

    # ── Internal methods ──────────────────────────────────────────────────────

    async def _exchange_for_short_lived(self, code: str, redirect_uri: str) -> OAuthTokenResponse:
        """Exchange authorization code for a short-lived token."""
        payload = {
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(TOKEN_EXCHANGE_URL, data=payload)

        if response.status_code != 200:
            error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            error_msg = error_data.get("error_message", f"HTTP {response.status_code}")
            raise SocialPublishingError(f"Instagram token exchange failed: {error_msg}")

        data = response.json()
        # Response structure: {"data": [{"access_token": ..., "user_id": ..., "permissions": ...}]}
        # OR for older format: {"access_token": ..., "user_id": ...}
        if "data" in data and isinstance(data["data"], list):
            token_data = data["data"][0]
        else:
            token_data = data

        return OAuthTokenResponse(
            access_token=token_data.get("access_token", ""),
            refresh_token="",
            expires_in_seconds=3600,  # Short-lived = 1 hour
            token_type="bearer",
            scopes=REQUIRED_SCOPES,
        )

    async def _exchange_for_long_lived(self, short_lived_token: str) -> OAuthTokenResponse:
        """Exchange a short-lived token for a long-lived one (60 days)."""
        params = {
            "grant_type": "ig_exchange_token",
            "client_secret": self._app_secret,
            "access_token": short_lived_token,
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(LONG_LIVED_TOKEN_URL, params=params)

        if response.status_code != 200:
            raise SocialPublishingError("Failed to exchange for long-lived Instagram token")

        data = response.json()
        access_token = data.get("access_token", "")

        return OAuthTokenResponse(
            access_token=access_token,
            # Instagram uses the access token itself for refresh (no separate refresh token)
            refresh_token=access_token,
            expires_in_seconds=int(data.get("expires_in", LONG_LIVED_TOKEN_SECONDS)),
            token_type=data.get("token_type", "bearer"),
            scopes=REQUIRED_SCOPES,
        )


def _map_account_type(ig_type: str) -> str:
    """Map Instagram's account_type field to Trendzzo's account types."""
    if ig_type in ("business",):
        return "business"
    if ig_type in ("media_creator", "creator"):
        return "creator"
    return "personal"
