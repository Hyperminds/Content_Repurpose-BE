"""Retry policy — classifies failures and computes backoff delays.

Determines:
  1. Whether a failure is retryable
  2. What category it falls into
  3. When the next retry should happen (exponential backoff with jitter)

The worker calls this after each failed publish attempt to decide what to do.
"""

import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.social_publishing.domain.models import PublishingResult
from app.social_publishing.jobs.models import FailureCategory, MAX_ATTEMPTS

# Base delay for exponential backoff (seconds)
_BASE_DELAY_SECONDS = 30

# Maximum backoff cap (2 hours)
_MAX_DELAY_SECONDS = 7200

# Jitter range (±20% of computed delay)
_JITTER_FACTOR = 0.2

# Keywords for failure classification (lowercase matching)
_AUTH_KEYWORDS = ("unauthorized", "401", "403", "token expired", "auth", "reauth")
_RATE_LIMIT_KEYWORDS = ("rate limit", "429", "too many requests", "throttl")
_PERMANENT_KEYWORDS = ("invalid content", "content policy", "banned", "not found", "404")
_PLATFORM_DOWN_KEYWORDS = ("503", "502", "504", "service unavailable", "gateway")


def classify_failure(result: PublishingResult) -> FailureCategory:
    """Determine the failure category from a PublishingResult."""
    if result.success:
        return FailureCategory.UNKNOWN  # shouldn't happen, but defensive

    error = (result.error_message or "").lower()

    if _matches_any(error, _AUTH_KEYWORDS):
        return FailureCategory.AUTH_EXPIRED

    if _matches_any(error, _RATE_LIMIT_KEYWORDS):
        return FailureCategory.RATE_LIMITED

    if _matches_any(error, _PERMANENT_KEYWORDS):
        return FailureCategory.PERMANENT

    if _matches_any(error, _PLATFORM_DOWN_KEYWORDS):
        return FailureCategory.PLATFORM_DOWN

    if result.retryable:
        return FailureCategory.TEMPORARY

    return FailureCategory.PERMANENT


def is_retryable(category: FailureCategory, attempts: int, max_attempts: int = MAX_ATTEMPTS) -> bool:
    """Determine if a job should be retried based on category and attempt count."""
    if attempts >= max_attempts:
        return False

    # Permanent failures and auth failures are never auto-retried
    if category == FailureCategory.PERMANENT:
        return False

    if category == FailureCategory.AUTH_EXPIRED:
        # Auth failures: retry once (token refresh might fix it), then stop
        return attempts < 2

    # Temporary, rate-limited, platform-down: retry up to max
    return True


def compute_next_retry_at(
    attempts: int,
    category: FailureCategory,
    retry_after_seconds: Optional[int] = None,
) -> datetime:
    """
    Compute when the next retry should happen.

    Uses exponential backoff with jitter. Rate-limited failures respect
    the platform's retry-after header if provided.
    """
    now = datetime.now(timezone.utc)

    # If the platform told us when to retry (rate limiting), respect it
    if retry_after_seconds and category == FailureCategory.RATE_LIMITED:
        return now + timedelta(seconds=retry_after_seconds)

    # Exponential backoff: base * 2^(attempts-1)
    delay = _BASE_DELAY_SECONDS * (2 ** max(0, attempts - 1))

    # Cap at maximum
    delay = min(delay, _MAX_DELAY_SECONDS)

    # Add jitter to prevent thundering herd
    jitter = delay * _JITTER_FACTOR
    delay = delay + random.uniform(-jitter, jitter)

    # Ensure minimum 10-second delay
    delay = max(10.0, delay)

    return now + timedelta(seconds=delay)


def _matches_any(text: str, keywords: tuple) -> bool:
    """Check if text contains any of the keywords."""
    return any(kw in text for kw in keywords)
