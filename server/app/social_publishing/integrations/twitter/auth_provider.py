"""X (Twitter) OAuth 2.0 auth provider — Authorization Code flow WITH PKCE.

Implements the SocialAuthProvider protocol plus two extra hooks the connection
service uses when a provider requires PKCE:
  - create_pkce_challenge() -> PKCEChallenge
  - get_auth_url(state, redirect_uri, code_challenge=...)
  - exchange_code(code, redirect_uri, code_verifier=...)

Flow:
  1. Generate PKCE verifier/challenge (S256)
  2. Redirect user to the authorize dialog with the challenge
  3. On callback, exchange code + verifier for access + refresh tokens
  4. Fetch account identity via GET /2/users/me

X access tokens are short-lived (~2h); a refresh_token (via offline.access)
is stored so the TokenRefreshWorker can renew them.
"""

import base64
import hashlib
import os
import secrets
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.social_publishing.domain.enums import AccountCapability, SocialPlatform
from app.social_publishing.domain.exceptions import SocialPublishingError
from app.social_publishing.auth.provider import (
    AccountInfo,
    OAuthTokenResponse,
    PKCEChallenge,
)
from app.social_publishing.integrations.twitter.constants import (
    ACCESS_TOKEN_SECONDS,
    AUTHORIZATION_URL,
    HTTP_TIMEOUT,
    PKCE_METHOD,
    PKCE_VERIFIER_BYTES,
    REQUIRED_SCOPES,
    TOKEN_URL,
    USERS_ME_URL,
)


class TwitterAuthProvider:
    """X (Twitter) OAuth 2.0 provider using Authorization Code + PKCE."""

    def __init__(self) -> None:
        self._client_id = os.getenv("TWITTER_CLIENT_ID", "")
        self._client_secret = os.getenv("TWITTER_CLIENT_SECRET", "")

    @property
    def platform(self) -> SocialPlatform:
        return SocialPlatform.TWITTER

    @property
    def required_scopes(self) -> list[str]:
        return list(REQUIRED_SCOPES)

    # ── PKCE ──────────────────────────────────────────────────────────────────

    def create_pkce_challenge(self) -> PKCEChallenge:
        """
        Generate a PKCE verifier/challenge pair (S256).

        The verifier is stored server-side (in the OAuth state); the challenge
        is sent to X in the authorization URL.
        """
        verifier = secrets.token_urlsafe(PKCE_VERIFIER_BYTES)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return PKCEChallenge(verifier=verifier, challenge=challenge, method=PKCE_METHOD)

    # ── Authorization URL ──────────────────────────────────────────────────────

    def get_auth_url(
        self, state: str, redirect_uri: str, code_challenge: str = ""
    ) -> str:
        """Build the X OAuth 2.0 authorization URL (with PKCE challenge)."""
        if not self._client_id:
            raise SocialPublishingError(
                "X (Twitter) OAuth not configured (missing TWITTER_CLIENT_ID)"
            )
        if not code_challenge:
            raise SocialPublishingError("X OAuth requires a PKCE code_challenge")

        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(REQUIRED_SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": PKCE_METHOD,
        }
        return f"{AUTHORIZATION_URL}?{urlencode(params)}"

    # ── Token exchange ─────────────────────────────────────────────────────────

    async def exchange_code(
        self, code: str, redirect_uri: str, code_verifier: str = ""
    ) -> OAuthTokenResponse:
        """Exchange authorization code + PKCE verifier for tokens."""
        if not self._client_id:
            raise SocialPublishingError("X (Twitter) OAuth credentials not configured")
        if not code_verifier:
            raise SocialPublishingError("X OAuth requires the PKCE code_verifier")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "code_verifier": code_verifier,
        }

        response = await self._token_request(data)
        payload = response.json()

        return OAuthTokenResponse(
            access_token=payload.get("access_token", ""),
            refresh_token=payload.get("refresh_token", ""),
            expires_in_seconds=int(payload.get("expires_in", ACCESS_TOKEN_SECONDS)),
            token_type=payload.get("token_type", "bearer"),
            scopes=REQUIRED_SCOPES,
        )

    # ── Account info ───────────────────────────────────────────────────────────

    async def get_account_info(self, access_token: str) -> AccountInfo:
        """Retrieve the X user's identity via GET /2/users/me."""
        params = {"user.fields": "username,name,profile_image_url"}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                USERS_ME_URL,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if response.status_code != 200:
            raise SocialPublishingError(
                f"X profile fetch failed (HTTP {response.status_code})"
            )

        data = response.json().get("data", {})
        username = data.get("username", "")
        return AccountInfo(
            platform_account_id=data.get("id", ""),
            account_name=data.get("name") or username or "X User",
            account_type="personal",
            email="",  # X does not return email via /2/users/me
            profile_url=f"https://x.com/{username}" if username else "",
            avatar_url=data.get("profile_image_url", ""),
        )

    # ── Capabilities ───────────────────────────────────────────────────────────

    async def detect_capabilities(
        self, access_token: str, account_info: AccountInfo
    ) -> list[AccountCapability]:
        """
        Detect publishing capabilities.

        A connected X account with tweet.write can publish text, images, and
        video. Actual posting still depends on the app's API access tier, which
        is enforced by X at publish time (surfaced as ACCESS_TIER_REQUIRED).
        """
        return [
            AccountCapability.TEXT_PUBLISHING,
            AccountCapability.IMAGE_PUBLISHING,
            AccountCapability.VIDEO_PUBLISHING,
            AccountCapability.SCHEDULED_PUBLISHING,
        ]

    # ── Token refresh ──────────────────────────────────────────────────────────

    async def refresh_token(self, refresh_token: str) -> Optional[OAuthTokenResponse]:
        """
        Refresh an X access token.

        X rotates refresh tokens: each refresh returns a NEW refresh_token that
        must replace the stored one. Returns None if refresh fails.
        """
        if not refresh_token or not self._client_id:
            return None

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
        }

        try:
            response = await self._token_request(data)
        except SocialPublishingError:
            return None

        payload = response.json()
        return OAuthTokenResponse(
            access_token=payload.get("access_token", ""),
            refresh_token=payload.get("refresh_token", refresh_token),
            expires_in_seconds=int(payload.get("expires_in", ACCESS_TOKEN_SECONDS)),
            token_type=payload.get("token_type", "bearer"),
            scopes=REQUIRED_SCOPES,
        )

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _token_request(self, data: dict) -> httpx.Response:
        """
        POST to the X token endpoint.

        For confidential clients, X requires HTTP Basic auth with the client
        id/secret. For public clients (no secret), the client_id in the body is
        sufficient. We send Basic auth only when a secret is configured.
        """
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        auth = None
        if self._client_secret:
            auth = (self._client_id, self._client_secret)

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                TOKEN_URL, data=data, headers=headers, auth=auth
            )

        if response.status_code != 200:
            raise SocialPublishingError(
                f"X token request failed (HTTP {response.status_code})"
            )
        return response
