"""Shared FastAPI dependencies for the social publishing module.

Provides service instances (with repository injection) and tenant identity
resolution, keeping route handlers thin.
"""

from app.core.identity import org_id_from_user
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository
from app.social_publishing.repositories.publishing_jobs_repository import PublishingJobsRepository
from app.social_publishing.services.social_accounts_service import SocialAccountsService
from app.social_publishing.services.social_posts_service import SocialPostsService
from app.social_publishing.services.publishing_orchestrator import PublishingOrchestrator

# ── Singleton instances ───────────────────────────────────────────────────────
# Repositories are stateless (just hold a collection reference), safe as singletons.

_accounts_repo = SocialAccountsRepository()
_posts_repo = SocialPostsRepository()
_jobs_repo = PublishingJobsRepository()
_publisher_registry = PublisherRegistry()

_accounts_service = SocialAccountsService(_accounts_repo)
_posts_service = SocialPostsService(_posts_repo, _accounts_repo)
_orchestrator = PublishingOrchestrator(_posts_repo, _jobs_repo, _publisher_registry)


def get_accounts_service() -> SocialAccountsService:
    return _accounts_service


def get_posts_service() -> SocialPostsService:
    return _posts_service


def get_orchestrator() -> PublishingOrchestrator:
    return _orchestrator


def get_publisher_registry() -> PublisherRegistry:
    return _publisher_registry


def get_posts_repo() -> SocialPostsRepository:
    return _posts_repo


def resolve_tenant_id(user: dict) -> str:
    """Extract the tenant_id from a decoded JWT payload."""
    return org_id_from_user(user)
