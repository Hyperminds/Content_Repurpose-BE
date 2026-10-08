"""LinkedIn OAuth 2.0 auth provider.

Implements the SocialAuthProvider protocol using LinkedIn's 3-legged OAuth flow.
Detects both member publishing capabilities and organization admin roles.

OAuth endpoints:
  - Authorization: https://www.linkedin.com/oauth/v2/authorization
  - Token exchange: https://www.linkedin.com/oauth/v2/accessToken
  - User info: https://api.linkedin.com/v2/userinfo
"""

import os
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.social_publishing.domain.enums import AccountCapability, SocialPlatform
from app.social_publishing.domain.exceptions import SocialPublishingError
from app.social_publishing.auth.provider import AccountInfo, OAuthTokenResponse
from app.social_publishing.integrations.linkedin.constants import (
    ACCESS_TOKEN_SECONDS,
    API_BASE,
    API_VERSION,
    AUTHORIZATION_URL,
    HTTP_TIMEOUT,
    MEMBER_SCOPES,
    PUBLISHING_ROLES,
    TOKEN_URL,
    USERINFO_URL,
)


class LinkedInAuthProvider:
    """LinkedIn OAuth 2.0 provider with organization role detection."""

    def __init__(self) -> None:
        self._client_id = os.getenv("LINKEDIN_CLIENT_ID", "")
        self._client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "")

    @property
    def platform(self) -> SocialPlatform:
        return SocialPlatform.LINKEDIN

    @property
    def required_scopes(self) -> list[str]:
        return list(MEMBER_SCOPES)

    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """Build the LinkedIn OAuth authorization URL."""
        if not self._client_id:
            raise SocialPublishingError("LinkedIn OAuth not configured (missing LINKEDIN_CLIENT_ID)")

        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(MEMBER_SCOPES),
        }
        return f"{AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokenResponse:
        """Exchange authorization code for access token."""
        if not self._client_id or not self._client_secret:
            raise SocialPublishingError("LinkedIn OAuth credentials not configured")

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if response.status_code != 200:
            raise SocialPublishingError(
                f"LinkedIn token exchange failed (HTTP {response.status_code})"
            )

        data = response.json()
        return OAuthTokenResponse(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expires_in_seconds=int(data.get("expires_in", ACCESS_TOKEN_SECONDS)),
            token_type=data.get("token_type", "Bearer"),
            scopes=MEMBER_SCOPES,
        )

    async def get_account_info(self, access_token: str) -> AccountInfo:
        """Retrieve the LinkedIn member's profile information."""
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if response.status_code != 200:
            raise SocialPublishingError(
                f"LinkedIn profile fetch failed (HTTP {response.status_code})"
            )

        data = response.json()
        return AccountInfo(
            platform_account_id=data.get("sub", ""),
            account_name=data.get("name", "LinkedIn User"),
            account_type="personal",
            email=data.get("email", ""),
            profile_url=f"https://www.linkedin.com/in/{data.get('sub', '')}",
            avatar_url=data.get("picture", ""),
        )

    async def detect_capabilities(
        self, access_token: str, account_info: AccountInfo
    ) -> list[AccountCapability]:
        """
        Detect publishing capabilities for the connected LinkedIn account.

        Always grants member publishing (text + image).
        Additionally checks if the user has admin roles on any organizations,
        which would enable organization publishing.
        """
        capabilities = [
            AccountCapability.TEXT_PUBLISHING,
            AccountCapability.IMAGE_PUBLISHING,
            AccountCapability.VIDEO_PUBLISHING,
        ]

        # Check for organization admin access
        org_roles = await self._fetch_organization_roles(access_token)
        if org_roles:
            capabilities.append(AccountCapability.ORGANIZATION_PUBLISHING)

        return capabilities

    async def refresh_token(self, refresh_token: str) -> Optional[OAuthTokenResponse]:
        """
        Refresh a LinkedIn access token.

        Programmatic refresh is only available for approved partner apps.
        Returns None if refresh is not supported or fails.
        """
        if not refresh_token:
            return None

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if response.status_code != 200:
            return None

        data = response.json()
        return OAuthTokenResponse(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", refresh_token),
            expires_in_seconds=int(data.get("expires_in", ACCESS_TOKEN_SECONDS)),
            token_type=data.get("token_type", "Bearer"),
            scopes=MEMBER_SCOPES,
        )

    # ── Organization role detection ───────────────────────────────────────────

    async def _fetch_organization_roles(self, access_token: str) -> list[dict]:
        """
        Fetch organizations where the member has a publishing-eligible role.

        Uses the organizationalEntityAcls endpoint to find orgs where the member
        has ADMINISTRATOR, DIRECT_SPONSORED_CONTENT_POSTER, or CONTENT_ADMIN roles.

        Returns a list of {org_id, role} dicts for eligible orgs. Returns empty
        list if the endpoint is unavailable (scope not granted or not approved).
        """
        url = f"{API_BASE}/organizationalEntityAcls"
        params = {
            "q": "roleAssignee",
            "projection": "(elements*(organizationalTarget,role))",
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Linkedin-Version": API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.get(url, params=params, headers=headers)

            if response.status_code != 200:
                # Scope not granted or API not available — not an error,
                # just means no org publishing capability
                return []

            data = response.json()
            elements = data.get("elements", [])

            eligible_orgs = []
            for element in elements:
                role = element.get("role", "")
                org_urn = element.get("organizationalTarget", "")
                if role in PUBLISHING_ROLES and org_urn:
                    org_id = org_urn.split(":")[-1] if ":" in org_urn else org_urn
                    eligible_orgs.append({"org_id": org_id, "role": role, "urn": org_urn})

            return eligible_orgs

        except Exception:
            # Network error or timeout — don't fail the connection,
            # just skip org publishing capability
            return []
