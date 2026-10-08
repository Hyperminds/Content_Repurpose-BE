"""Unit tests for domain models and state machine."""

import pytest

from app.social_publishing.domain.enums import (
    PostStatus,
    SocialPlatform,
    VALID_TRANSITIONS,
)
from app.social_publishing.domain.exceptions import (
    InvalidStateTransition,
    TenantAccessDenied,
    PostNotFound,
    AccountNotFound,
    PlatformNotSupported,
    SchedulingError,
    ValidationError,
    SocialPublishingError,
)
from app.social_publishing.domain.models import (
    SocialAccount,
    SocialPost,
    PublishingJob,
    PublishingResult,
)


class TestPostStatus:
    def test_all_statuses_exist(self):
        # Classic native-publishing lifecycle states.
        classic = {
            PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.QUEUED,
            PostStatus.PUBLISHING, PostStatus.PUBLISHED, PostStatus.FAILED,
            PostStatus.RETRYING, PostStatus.CANCELLED,
        }
        assert classic.issubset(set(PostStatus))
        # Hybrid-publishing lifecycle states (Phase 10).
        hybrid = {
            PostStatus.VERIFYING, PostStatus.ACTION_REQUIRED,
            PostStatus.WAITING_FOR_USER, PostStatus.UNKNOWN,
        }
        assert hybrid.issubset(set(PostStatus))
        assert len(PostStatus) == 12

    def test_terminal_states(self):
        terminals = PostStatus.terminal_states()
        assert PostStatus.PUBLISHED in terminals
        assert PostStatus.CANCELLED in terminals
        assert PostStatus.DRAFT not in terminals

    def test_active_states(self):
        actives = PostStatus.active_states()
        assert PostStatus.QUEUED in actives
        assert PostStatus.PUBLISHING in actives
        assert PostStatus.RETRYING in actives
        assert PostStatus.DRAFT not in actives

    def test_string_values(self):
        assert PostStatus.DRAFT.value == "draft"
        assert PostStatus.PUBLISHED.value == "published"


class TestStateTransitions:
    def test_draft_can_go_to_scheduled(self):
        assert PostStatus.SCHEDULED in VALID_TRANSITIONS[PostStatus.DRAFT]

    def test_draft_can_go_to_cancelled(self):
        assert PostStatus.CANCELLED in VALID_TRANSITIONS[PostStatus.DRAFT]

    def test_draft_cannot_go_to_published(self):
        assert PostStatus.PUBLISHED not in VALID_TRANSITIONS[PostStatus.DRAFT]

    def test_scheduled_can_go_to_queued(self):
        assert PostStatus.QUEUED in VALID_TRANSITIONS[PostStatus.SCHEDULED]

    def test_scheduled_can_go_to_cancelled(self):
        assert PostStatus.CANCELLED in VALID_TRANSITIONS[PostStatus.SCHEDULED]

    def test_scheduled_can_go_back_to_draft(self):
        assert PostStatus.DRAFT in VALID_TRANSITIONS[PostStatus.SCHEDULED]

    def test_published_has_no_transitions(self):
        assert VALID_TRANSITIONS[PostStatus.PUBLISHED] == set()

    def test_cancelled_has_no_transitions(self):
        assert VALID_TRANSITIONS[PostStatus.CANCELLED] == set()

    def test_failed_can_retry(self):
        assert PostStatus.RETRYING in VALID_TRANSITIONS[PostStatus.FAILED]

    def test_failed_can_cancel(self):
        assert PostStatus.CANCELLED in VALID_TRANSITIONS[PostStatus.FAILED]

    def test_failed_can_reschedule(self):
        assert PostStatus.SCHEDULED in VALID_TRANSITIONS[PostStatus.FAILED]

    def test_retrying_goes_to_queued(self):
        assert PostStatus.QUEUED in VALID_TRANSITIONS[PostStatus.RETRYING]

    def test_publishing_can_succeed(self):
        assert PostStatus.PUBLISHED in VALID_TRANSITIONS[PostStatus.PUBLISHING]

    def test_publishing_can_fail(self):
        assert PostStatus.FAILED in VALID_TRANSITIONS[PostStatus.PUBLISHING]


class TestSocialPlatform:
    def test_all_platforms_exist(self):
        # 9 platforms: linkedin, instagram, twitter, reddit, medium, meta,
        # facebook (user-assisted Hermes target), threads, quora.
        assert len(SocialPlatform) == 9
        assert SocialPlatform.FACEBOOK.value == "facebook"

    def test_values_are_lowercase(self):
        for p in SocialPlatform:
            assert p.value == p.value.lower()


class TestPublishingResult:
    def test_success_result(self):
        result = PublishingResult(success=True, platform_post_id="post_123")
        assert result.success is True
        assert result.platform_post_id == "post_123"
        assert result.error_message is None

    def test_failure_result(self):
        result = PublishingResult(success=False, error_message="Rate limited", retryable=True)
        assert result.success is False
        assert result.retryable is True

    def test_non_retryable_failure(self):
        result = PublishingResult(success=False, error_message="Content banned", retryable=False)
        assert result.retryable is False


class TestExceptions:
    def test_base_exception(self):
        exc = SocialPublishingError("test error")
        assert str(exc) == "test error"
        assert exc.message == "test error"

    def test_tenant_access_denied(self):
        exc = TenantAccessDenied("post")
        assert "post" in exc.message
        assert "Access denied" in exc.message

    def test_invalid_state_transition(self):
        exc = InvalidStateTransition("draft", "published")
        assert "draft" in exc.message
        assert "published" in exc.message

    def test_post_not_found(self):
        exc = PostNotFound("abc123")
        assert "abc123" in exc.message

    def test_platform_not_supported(self):
        exc = PlatformNotSupported("tiktok")
        assert "tiktok" in exc.message

    def test_scheduling_error(self):
        exc = SchedulingError("Time in the past")
        assert "Time in the past" in exc.message

    def test_validation_error(self):
        exc = ValidationError("Content empty")
        assert "Content empty" in exc.message

    def test_all_inherit_from_base(self):
        exceptions = [
            TenantAccessDenied(),
            InvalidStateTransition("a", "b"),
            PostNotFound(),
            AccountNotFound(),
            PlatformNotSupported(),
            SchedulingError(),
            ValidationError(),
        ]
        for exc in exceptions:
            assert isinstance(exc, SocialPublishingError)
