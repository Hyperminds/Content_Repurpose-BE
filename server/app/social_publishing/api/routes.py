"""FastAPI routes for the social publishing engine.

All routes require authentication and resolve tenant_id from the JWT.
Domain exceptions are caught and mapped to proper HTTP status codes.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from app.utils.jwt_handler import get_current_user
from app.social_publishing.api.dependencies import (
    get_accounts_service,
    get_posts_service,
    resolve_tenant_id,
)
from app.social_publishing.api.schemas import (
    CreateAccountRequest,
    UpdateAccountRequest,
    AccountResponse,
    CreatePostRequest,
    UpdatePostRequest,
    SchedulePostRequest,
    PostResponse,
    PostListResponse,
    PublishingStatusResponse,
)
from app.social_publishing.domain.exceptions import (
    SocialPublishingError,
    TenantAccessDenied,
    InvalidStateTransition,
    PostNotFound,
    AccountNotFound,
    PlatformNotSupported,
    SchedulingError,
    ValidationError,
)
from app.social_publishing.domain.models import SocialAccount, SocialPost

router = APIRouter(prefix="/social-publishing", tags=["social-publishing"])


# ── Exception → HTTP mapping ──────────────────────────────────────────────────

_STATUS_MAP = {
    TenantAccessDenied: 403,
    PostNotFound: 404,
    AccountNotFound: 404,
    PlatformNotSupported: 400,
    InvalidStateTransition: 409,
    SchedulingError: 400,
    ValidationError: 422,
}


def _raise_http(exc: SocialPublishingError) -> None:
    """Convert a domain exception to an HTTPException."""
    status = _STATUS_MAP.get(type(exc), 400)
    raise HTTPException(status_code=status, detail=exc.message)


# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/accounts", response_model=AccountResponse, status_code=201)
async def create_account(
    body: CreateAccountRequest,
    user: dict = Depends(get_current_user),
):
    """Create a new social account."""
    tenant_id = resolve_tenant_id(user)
    svc = get_accounts_service()
    try:
        account = await svc.create_account(
            tenant_id=tenant_id,
            platform=body.platform,
            account_name=body.account_name,
            platform_account_id=body.platform_account_id,
            provider_type=body.provider_type,
            provider_name=body.provider_name,
        )
    except SocialPublishingError as e:
        _raise_http(e)
    return _account_response(account)


@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(
    platform: Optional[str] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List social accounts for the current tenant."""
    tenant_id = resolve_tenant_id(user)
    svc = get_accounts_service()
    try:
        accounts = await svc.list_accounts(
            tenant_id=tenant_id,
            platform=platform,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
    except SocialPublishingError as e:
        _raise_http(e)
    return [_account_response(a) for a in accounts]


@router.get("/accounts/{account_id}", response_model=AccountResponse)
async def get_account(account_id: str, user: dict = Depends(get_current_user)):
    """Get a single social account."""
    tenant_id = resolve_tenant_id(user)
    svc = get_accounts_service()
    try:
        account = await svc.get_account(account_id, tenant_id)
    except SocialPublishingError as e:
        _raise_http(e)
    return _account_response(account)


@router.patch("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str,
    body: UpdateAccountRequest,
    user: dict = Depends(get_current_user),
):
    """Update a social account."""
    tenant_id = resolve_tenant_id(user)
    svc = get_accounts_service()
    updates = body.model_dump(exclude_none=True)
    try:
        account = await svc.update_account(account_id, tenant_id, updates)
    except SocialPublishingError as e:
        _raise_http(e)
    return _account_response(account)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: str, user: dict = Depends(get_current_user)):
    """Delete a social account."""
    tenant_id = resolve_tenant_id(user)
    svc = get_accounts_service()
    try:
        await svc.delete_account(account_id, tenant_id)
    except SocialPublishingError as e:
        _raise_http(e)


# ══════════════════════════════════════════════════════════════════════════════
# POSTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/posts", response_model=PostResponse, status_code=201)
async def create_post(
    body: CreatePostRequest,
    user: dict = Depends(get_current_user),
):
    """Create a new social post."""
    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()
    try:
        post = await svc.create_post(
            tenant_id=tenant_id,
            account_id=body.account_id,
            platform=body.platform,
            content=body.content,
            media_urls=body.media_urls,
            scheduled_at=body.scheduled_at,
        )
    except SocialPublishingError as e:
        _raise_http(e)
    return _post_response(post)


@router.post("/posts/publish-now", response_model=PostResponse, status_code=201)
async def publish_now(
    body: CreatePostRequest,
    user: dict = Depends(get_current_user),
):
    """Create a post and immediately queue it for publishing (no scheduling delay)."""
    from datetime import datetime, timezone
    from app.social_publishing.domain.enums import PostStatus
    from app.social_publishing.jobs.repository import JobRepository

    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()

    try:
        # Create the post in DRAFT first (no scheduled_at)
        post = await svc.create_post(
            tenant_id=tenant_id,
            account_id=body.account_id,
            platform=body.platform,
            content=body.content,
            media_urls=body.media_urls,
            scheduled_at=None,
        )
    except SocialPublishingError as e:
        _raise_http(e)

    # Immediately transition to QUEUED and enqueue a job
    from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository
    posts_repo = SocialPostsRepository()
    await posts_repo.update_status_by_id(post.id, PostStatus.QUEUED)

    job_repo = JobRepository()
    from app.social_publishing.domain.enums import SocialPlatform
    await job_repo.enqueue(
        tenant_id=tenant_id,
        post_id=post.id,
        account_id=body.account_id,
        platform=SocialPlatform(body.platform),
        idempotency_key=f"publish_now_{post.id}",
    )

    # Re-read to get updated status
    updated_post = await posts_repo.find_by_id(post.id, tenant_id)
    return _post_response(updated_post)


@router.get("/posts", response_model=list[PostResponse])
async def list_posts(
    status: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List social posts for the current tenant."""
    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()
    try:
        posts = await svc.list_posts(
            tenant_id=tenant_id,
            status=status,
            platform=platform,
            account_id=account_id,
            limit=limit,
            offset=offset,
        )
    except SocialPublishingError as e:
        _raise_http(e)
    return [_post_response(p) for p in posts]


@router.get("/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: str, user: dict = Depends(get_current_user)):
    """Get a single social post."""
    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()
    try:
        post = await svc.get_post(post_id, tenant_id)
    except SocialPublishingError as e:
        _raise_http(e)
    return _post_response(post)


@router.patch("/posts/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: str,
    body: UpdatePostRequest,
    user: dict = Depends(get_current_user),
):
    """Update a social post (only DRAFT/SCHEDULED)."""
    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()
    try:
        post = await svc.update_post(
            post_id=post_id,
            tenant_id=tenant_id,
            content=body.content,
            media_urls=body.media_urls,
            scheduled_at=body.scheduled_at,
        )
    except SocialPublishingError as e:
        _raise_http(e)
    return _post_response(post)


@router.post("/posts/{post_id}/schedule", response_model=PostResponse)
async def schedule_post(
    post_id: str,
    body: SchedulePostRequest,
    user: dict = Depends(get_current_user),
):
    """Schedule a draft post for future publishing."""
    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()
    try:
        post = await svc.schedule_post(post_id, tenant_id, body.scheduled_at)
    except SocialPublishingError as e:
        _raise_http(e)
    return _post_response(post)


@router.post("/posts/{post_id}/cancel", response_model=PostResponse)
async def cancel_post(post_id: str, user: dict = Depends(get_current_user)):
    """Cancel a post."""
    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()
    try:
        post = await svc.cancel_post(post_id, tenant_id)
    except SocialPublishingError as e:
        _raise_http(e)
    return _post_response(post)


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(post_id: str, user: dict = Depends(get_current_user)):
    """Delete a post (only DRAFT or CANCELLED)."""
    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()
    try:
        await svc.delete_post(post_id, tenant_id)
    except SocialPublishingError as e:
        _raise_http(e)


@router.get("/posts/{post_id}/status", response_model=PublishingStatusResponse)
async def get_publishing_status(post_id: str, user: dict = Depends(get_current_user)):
    """Get the publishing status of a post."""
    tenant_id = resolve_tenant_id(user)
    svc = get_posts_service()
    try:
        post = await svc.get_post(post_id, tenant_id)
    except SocialPublishingError as e:
        _raise_http(e)
    return PublishingStatusResponse(
        post_id=post.id,
        status=post.status.value,
        platform_post_id=post.platform_post_id,
        failure_reason=post.failure_reason,
        retry_count=post.retry_count,
        published_at=post.published_at.isoformat() if post.published_at else None,
    )


@router.get("/metrics")
async def get_publishing_metrics(user: dict = Depends(get_current_user)):
    """Get publishing system metrics (job queue stats)."""
    from app.social_publishing.jobs.repository import JobRepository
    job_repo = JobRepository()
    queue_stats = await job_repo.count_by_status()
    pending = await job_repo.pending_count()

    return {
        "queue": {
            "pending_jobs": pending,
            "by_status": queue_stats,
        },
    }


@router.get("/rate-limits")
async def get_rate_limits(user: dict = Depends(get_current_user)):
    """Get per-platform rate limit status for the current tenant."""
    tenant_id = resolve_tenant_id(user)
    from app.social_publishing.jobs.rate_limiter import PublishingRateLimiter
    limiter = PublishingRateLimiter()
    return await limiter.get_all_limits(tenant_id)


# ── Response builders ─────────────────────────────────────────────────────────

def _account_response(account: SocialAccount) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        tenant_id=account.tenant_id,
        platform=account.platform.value,
        account_name=account.account_name,
        platform_account_id=account.platform_account_id,
        account_type=account.account_type,
        is_active=account.is_active,
        connection_status=account.connection_status.value,
        provider_type=account.provider_type.value,
        provider_name=account.provider_name,
        has_access_token=account.has_access_token,
        capabilities=[c.value for c in account.capabilities],
        scopes=account.scopes,
        token_expires_at=account.token_expires_at.isoformat() if account.token_expires_at else None,
        created_at=account.created_at.isoformat() if account.created_at else None,
        updated_at=account.updated_at.isoformat() if account.updated_at else None,
    )


def _post_response(post: SocialPost) -> PostResponse:
    return PostResponse(
        id=post.id,
        tenant_id=post.tenant_id,
        account_id=post.account_id,
        platform=post.platform.value,
        status=post.status.value,
        content=post.content,
        media_urls=post.media_urls,
        scheduled_at=post.scheduled_at.isoformat() if post.scheduled_at else None,
        published_at=post.published_at.isoformat() if post.published_at else None,
        failure_reason=post.failure_reason,
        retry_count=post.retry_count,
        max_retries=post.max_retries,
        platform_post_id=post.platform_post_id,
        created_at=post.created_at.isoformat() if post.created_at else None,
        updated_at=post.updated_at.isoformat() if post.updated_at else None,
    )
