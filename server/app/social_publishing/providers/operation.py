"""Operation identity and verify-before-retry policy (Phase 11).

Every publishing operation carries a stable operation_id derived from:

    trendzzo_job_id + account_id + content_version

The same logical publish (same job, same account, same content revision) always
produces the same operation_id, so a provider can be handed the id to
de-duplicate and Trendzzo can correlate attempts. Retrying a job with unchanged
content reuses the id (idempotent); editing the content changes the version and
therefore the id (a genuinely new operation).

This module also centralizes the UNKNOWN handling rule: an uncertain outcome is
NEVER blindly retried. It must be verified first (Phase 10 / 14).
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from app.social_publishing.domain.models import SocialAccount, SocialPost
from app.social_publishing.jobs.models import QueuedJob
from app.social_publishing.providers.errors import (
    ExternalResultStatus,
    PublishInstruction,
    ProviderResult,
)


def content_version(post: SocialPost) -> str:
    """
    A short, stable hash of the publishable content.

    Changes only when the text or media set changes, so an edited post yields a
    new operation_id while a plain retry keeps the same one.
    """
    basis = "\u0000".join([post.content or "", *sorted(post.media_urls or [])])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def build_operation_id(job_id: str, account_id: str, version: str) -> str:
    """Compose the canonical operation id: job + account + content version."""
    return f"op_{job_id}_{account_id}_{version}"


def instruction_from_job(
    job: QueuedJob, post: SocialPost, account: SocialAccount
) -> PublishInstruction:
    """
    Build a normalized, credential-free PublishInstruction for a provider.

    `account_id` is set to the platform account id (what the provider needs to
    address the correct destination), matching the existing worker convention.
    """
    version = content_version(post)
    operation_id = build_operation_id(job.id, account.id, version)
    return PublishInstruction(
        operation_id=operation_id,
        tenant_id=job.tenant_id,
        account_id=account.platform_account_id or post.account_id,
        platform=job.platform.value,
        provider_name=account.provider_name,
        text=post.content or "",
        media_urls=list(post.media_urls or []),
        # account_record_id is the internal SocialAccount id (tenant-scoped),
        # used by BYOK/session lookups. Distinct from account_id above, which is
        # the external platform account id used to address the destination.
        meta={
            "content_version": version,
            "job_id": job.id,
            "account_record_id": account.id,
        },
    )


@dataclass
class RetryDecision:
    """Outcome of applying the verify-before-retry policy to a ProviderResult."""

    should_retry: bool
    needs_verification: bool
    needs_user_action: bool
    reason: str = ""


def decide_after_result(result: ProviderResult) -> RetryDecision:
    """
    Decide what the engine should do next given a normalized ProviderResult.

    Rules (Phase 10 / 11 / 14):
      - UNKNOWN            → verify first, NEVER blind-retry
      - ACTION_REQUIRED    → pause for the user (not a failure)
      - PUBLISHED          → done
      - FAILED             → retry only if the provider marked it retryable
    """
    if result.status == ExternalResultStatus.UNKNOWN:
        return RetryDecision(
            should_retry=False,
            needs_verification=True,
            needs_user_action=False,
            reason="Outcome uncertain — must verify before any retry",
        )

    if result.status == ExternalResultStatus.ACTION_REQUIRED:
        return RetryDecision(
            should_retry=False,
            needs_verification=False,
            needs_user_action=True,
            reason="Manual user action required to continue",
        )

    if result.status == ExternalResultStatus.PUBLISHED:
        return RetryDecision(
            should_retry=False, needs_verification=False, needs_user_action=False,
            reason="Published",
        )

    # FAILED — honor the provider's retryable flag only.
    return RetryDecision(
        should_retry=bool(result.retryable),
        needs_verification=False,
        needs_user_action=False,
        reason=result.error_message or "Failed",
    )
