"""Service for social account management.

Validates requests, enforces business rules, and delegates persistence to
the repository. All methods require tenant_id for isolation.
"""

from typing import Optional

from app.social_publishing.domain.enums import SocialPlatform
from app.social_publishing.domain.exceptions import (
    AccountNotFound,
    PlatformNotSupported,
    ValidationError,
)
from app.social_publishing.domain.models import SocialAccount
from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository
from app.services.logger import log


class SocialAccountsService:
    """Business logic for social account lifecycle."""

    def __init__(self, repo: SocialAccountsRepository) -> None:
        self._repo = repo

    async def create_account(
        self,
        tenant_id: str,
        platform: str,
        account_name: str,
        platform_account_id: str,
        provider_type: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> SocialAccount:
        """
        Create a new social account for a tenant.

        provider_type/provider_name are optional. When supplied (e.g. the UI
        adding a user-assisted Hermes account) they are persisted and the account
        is marked connected so it is immediately usable; otherwise the account
        keeps the default native/pending shape.
        """
        validated_platform = _validate_platform(platform)
        if not account_name.strip():
            raise ValidationError("Account name is required")
        if not platform_account_id.strip():
            raise ValidationError("Platform account ID is required")

        account = await self._repo.create(
            tenant_id=tenant_id,
            platform=validated_platform,
            account_name=account_name.strip(),
            platform_account_id=platform_account_id.strip(),
            provider_type=provider_type,
            provider_name=provider_name,
        )
        log.info(
            "Social account created",
            tenant_id=tenant_id,
            platform=platform,
            account_id=account.id,
        )
        return account

    async def get_account(self, account_id: str, tenant_id: str) -> SocialAccount:
        """Get an account by id, enforcing tenant ownership."""
        account = await self._repo.find_by_id(account_id, tenant_id)
        if not account:
            raise AccountNotFound(account_id)
        return account

    async def list_accounts(
        self,
        tenant_id: str,
        platform: Optional[str] = None,
        active_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SocialAccount]:
        """List accounts for a tenant."""
        validated_platform = _validate_platform(platform) if platform else None
        return await self._repo.find_by_tenant(
            tenant_id=tenant_id,
            platform=validated_platform,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    async def update_account(
        self,
        account_id: str,
        tenant_id: str,
        updates: dict,
    ) -> SocialAccount:
        """Update account fields."""
        result = await self._repo.update(account_id, tenant_id, updates)
        if not result:
            raise AccountNotFound(account_id)
        return result

    async def deactivate_account(self, account_id: str, tenant_id: str) -> bool:
        """Deactivate (soft-delete) an account."""
        success = await self._repo.deactivate(account_id, tenant_id)
        if not success:
            raise AccountNotFound(account_id)
        log.info("Social account deactivated", account_id=account_id, tenant_id=tenant_id)
        return True

    async def delete_account(self, account_id: str, tenant_id: str) -> bool:
        """Hard-delete an account."""
        success = await self._repo.delete(account_id, tenant_id)
        if not success:
            raise AccountNotFound(account_id)
        log.info("Social account deleted", account_id=account_id, tenant_id=tenant_id)
        return True


def _validate_platform(platform: str) -> SocialPlatform:
    """Validate and convert a platform string to enum."""
    try:
        return SocialPlatform(platform.lower().strip())
    except ValueError:
        raise PlatformNotSupported(platform)
