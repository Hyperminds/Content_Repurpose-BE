"""OAuth state store — CSRF protection for OAuth flows.

Generates cryptographically-secure state tokens tied to a specific tenant and
user. The token is passed to the platform in the OAuth authorization URL, then
validated when the platform redirects back. This prevents:
  - CSRF attacks (attacker cannot forge a valid state)
  - Account-linking attacks (state is bound to the authenticated user)

Storage: MongoDB collection with TTL index for automatic expiry cleanup.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.database import db
from app.social_publishing.domain.enums import SocialPlatform
from app.social_publishing.domain.models import OAuthState

_collection = db["sp_oauth_states"]

# State tokens expire after this duration (prevents stale flows)
STATE_TTL_MINUTES = 10

# Token length in bytes (32 bytes = 64 hex chars — high entropy)
_TOKEN_BYTES = 32


class OAuthStateStore:
    """Manages CSRF-safe OAuth state tokens with TTL."""

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        platform: SocialPlatform,
        redirect_uri: str,
        code_verifier: str = "",
    ) -> OAuthState:
        """
        Generate a new state token and persist it.

        Args:
            code_verifier: Optional PKCE verifier for providers requiring
                OAuth 2.0 PKCE (e.g. X/Twitter). Stored server-side so it can
                be replayed at token-exchange time; never sent to the client.

        Returns the OAuthState with the token to include in the auth URL.
        """
        now = datetime.now(timezone.utc)
        state_token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = now + timedelta(minutes=STATE_TTL_MINUTES)

        doc = {
            "state_token": state_token,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "platform": platform.value,
            "redirect_uri": redirect_uri,
            "created_at": now,
            "expires_at": expires_at,
            "code_verifier": code_verifier,
        }
        await _collection.insert_one(doc)

        return OAuthState(
            state_token=state_token,
            tenant_id=tenant_id,
            user_id=user_id,
            platform=platform,
            redirect_uri=redirect_uri,
            created_at=now,
            expires_at=expires_at,
            code_verifier=code_verifier,
        )

    async def validate_and_consume(self, state_token: str) -> Optional[OAuthState]:
        """
        Validate a state token and consume it (one-time use).

        Returns the OAuthState if valid and not expired, or None if invalid.
        The token is deleted after consumption to prevent replay attacks.
        """
        now = datetime.now(timezone.utc)

        # Atomically find and delete (consume) — prevents race conditions
        doc = await _collection.find_one_and_delete({
            "state_token": state_token,
            "expires_at": {"$gt": now},
        })

        if not doc:
            return None

        return OAuthState(
            state_token=doc["state_token"],
            tenant_id=doc["tenant_id"],
            user_id=doc["user_id"],
            platform=SocialPlatform(doc["platform"]),
            redirect_uri=doc["redirect_uri"],
            created_at=doc["created_at"],
            expires_at=doc["expires_at"],
            code_verifier=doc.get("code_verifier", ""),
        )

    async def cleanup_expired(self) -> int:
        """Remove expired state tokens. Returns count deleted."""
        now = datetime.now(timezone.utc)
        result = await _collection.delete_many({"expires_at": {"$lte": now}})
        return result.deleted_count
