"""Token refresh worker — proactively refreshes expiring OAuth tokens.

Runs as a background asyncio task alongside the scheduler and publishing worker.
Periodically scans connected accounts whose tokens expire within a configurable
buffer window, then attempts to refresh them via the platform's auth provider.

If refresh succeeds: credentials are updated, connection stays CONNECTED.
If refresh fails: account is marked REAUTH_REQUIRED (user must re-connect).

This prevents silent publishing failures caused by expired tokens.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.database import db
from app.social_publishing.auth.provider import AuthProviderRegistry
from app.social_publishing.credentials.vault import CredentialVault
from app.social_publishing.domain.enums import ConnectionStatus, SocialPlatform
from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository
from app.services.logger import log

_collection = db["sp_social_accounts"]

# Refresh tokens that expire within this window
REFRESH_BUFFER_HOURS = 24

# How often to check for expiring tokens
POLL_INTERVAL_SECONDS = 900  # 15 minutes

# Max accounts to process per tick
BATCH_SIZE = 50


class TokenRefreshWorker:
    """Background worker that refreshes tokens before they expire."""

    def __init__(
        self,
        accounts_repo: SocialAccountsRepository,
        auth_registry: AuthProviderRegistry,
        vault: CredentialVault,
        poll_interval: int = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._accounts = accounts_repo
        self._auth_registry = auth_registry
        self._vault = vault
        self._poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("TokenRefreshWorker started", interval_s=self._poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        log.info("TokenRefreshWorker stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._refresh_expiring_tokens()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("TokenRefreshWorker loop error", error=str(e)[:200])
            await asyncio.sleep(self._poll_interval)

    async def _refresh_expiring_tokens(self) -> None:
        """Find accounts with tokens expiring soon and attempt refresh."""
        cutoff = datetime.now(timezone.utc) + timedelta(hours=REFRESH_BUFFER_HOURS)

        # Query accounts that are connected, have a token_expires_at within the buffer
        cursor = _collection.find({
            "connection_status": ConnectionStatus.CONNECTED.value,
            "has_access_token": True,
            "token_expires_at": {"$ne": None, "$lte": cutoff},
        }).limit(BATCH_SIZE)

        accounts = await cursor.to_list(length=BATCH_SIZE)

        if not accounts:
            return

        refreshed = 0
        failed = 0

        for doc in accounts:
            success = await self._refresh_single_account(doc)
            if success:
                refreshed += 1
            else:
                failed += 1

        if refreshed or failed:
            log.info(
                "Token refresh cycle complete",
                refreshed=refreshed,
                failed=failed,
            )

    async def _refresh_single_account(self, doc: dict) -> bool:
        """Attempt to refresh a single account's token."""
        account_id = str(doc["_id"])
        tenant_id = doc["tenant_id"]
        platform = doc.get("platform", "")

        # Get the auth provider for this platform
        provider = self._auth_registry.get(platform)
        if not provider:
            return False

        # Decrypt current credentials to get the refresh token
        encrypted_creds = doc.get("encrypted_credentials")
        if not encrypted_creds:
            return False

        try:
            decrypted = self._vault.decrypt_dict(encrypted_creds)
        except Exception:
            # Corrupted credentials — mark for reauth
            await self._mark_reauth_required(account_id, tenant_id)
            return False

        refresh_token = decrypted.get("refresh_token", "")
        if not refresh_token:
            # No refresh token available — some platforms don't support it
            # Check if token is actually expired yet
            expires_at = doc.get("token_expires_at")
            if expires_at and expires_at < datetime.now(timezone.utc):
                await self._mark_reauth_required(account_id, tenant_id)
                return False
            return True  # Not expired yet, just no refresh token

        # Attempt refresh
        try:
            token_response = await provider.refresh_token(refresh_token)
        except Exception as e:
            log.warning(
                "Token refresh failed",
                account_id=account_id,
                platform=platform,
                error=str(e)[:100],
            )
            await self._mark_reauth_required(account_id, tenant_id)
            return False

        if not token_response or not token_response.access_token:
            await self._mark_reauth_required(account_id, tenant_id)
            return False

        # Encrypt and store new credentials
        new_creds = {
            "access_token": token_response.access_token,
            "refresh_token": token_response.refresh_token or refresh_token,
            "token_type": token_response.token_type,
        }
        encrypted_new = self._vault.encrypt_dict(new_creds)

        # Calculate new expiry
        new_expires_at = None
        if token_response.expires_in_seconds > 0:
            new_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=token_response.expires_in_seconds
            )

        await self._accounts.update_credentials(
            account_id=account_id,
            tenant_id=tenant_id,
            encrypted_credentials=encrypted_new,
            token_expires_at=new_expires_at,
            connection_status=ConnectionStatus.CONNECTED,
        )

        log.info("Token refreshed", account_id=account_id, platform=platform)
        return True

    async def _mark_reauth_required(self, account_id: str, tenant_id: str) -> None:
        """Mark an account as needing re-authentication."""
        await self._accounts.update_connection_status(
            account_id, tenant_id, ConnectionStatus.REAUTH_REQUIRED
        )
        log.warning("Account marked REAUTH_REQUIRED", account_id=account_id)
