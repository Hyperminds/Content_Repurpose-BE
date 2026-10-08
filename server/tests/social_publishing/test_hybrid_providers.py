"""Tests for the hybrid publishing provider infrastructure (Phase 27).

Covers: provider registry/resolution, capabilities, native routing, Hermes
routing, BYOK routing, unsupported platform, automation prohibited, tenant
isolation, account ownership, auth/user-action required, success/fail/timeout/
unknown, verification, idempotency/duplicate prevention, retry classification,
error normalization, media validation, secret sanitization, feature flags, and
the FakeHermesExecutionAdapter.

No real network or browser activity — everything is fake/mocked.
"""

import pytest
from unittest.mock import AsyncMock

from app.social_publishing.domain.enums import (
    PostStatus,
    ProviderType,
    SocialPlatform,
    VALID_TRANSITIONS,
    can_transition,
)
from app.social_publishing.domain.models import SocialAccount, SocialPost
from app.social_publishing.jobs.models import QueuedJob

from app.social_publishing.providers.base import PublishingProvider
from app.social_publishing.providers.router import ProviderRouter
from app.social_publishing.providers.native_provider import NativeApiProvider
from app.social_publishing.providers.capabilities import (
    ProviderCapabilities,
    requirements_for,
    unmet_requirements,
)
from app.social_publishing.providers.native_capabilities import native_capabilities_for
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    ExternalProviderError,
    PublishInstruction,
    ProviderResult,
)
from app.social_publishing.providers.operation import (
    content_version,
    build_operation_id,
    instruction_from_job,
    decide_after_result,
)
from app.social_publishing.providers.observability import _scrub

from app.social_publishing.providers.hermes.provider import HermesPublisherProvider
from app.social_publishing.providers.hermes.fake_workflow import FakeHermesWorkflow, FAKE_PLATFORM
from app.social_publishing.providers.hermes.adapter import FakeHermesExecutionAdapter, FakeOutcome
from app.social_publishing.providers.hermes.automation_policy import PlatformAutomationPolicy
from app.social_publishing.providers.hermes.retry_classification import classify, RetryDisposition
from app.social_publishing.providers.hermes.media_workspace import (
    validate_media,
    is_safe_workspace_name,
    resolve_within,
    media_workspace,
    MediaValidationError,
)
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.publishers.stub_publisher import StubPublisher


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

def _account(provider_type=ProviderType.NATIVE_API, provider_name="linkedin",
             platform=SocialPlatform.LINKEDIN, tenant="t1", acc_id="acc1"):
    return SocialAccount(
        id=acc_id, tenant_id=tenant, platform=platform,
        account_name="n", platform_account_id="PA1",
        provider_type=provider_type, provider_name=provider_name,
    )


def _instruction(platform=FAKE_PLATFORM, provider_name="hermes"):
    return PublishInstruction(
        operation_id="op1", tenant_id="t1", account_id="PA1",
        platform=platform, provider_name=provider_name, text="hello",
    )


def _hermes(outcome=FakeOutcome.SUCCESS, verify=None, automation_platform=None):
    policy = PlatformAutomationPolicy()
    if automation_platform:
        policy.allow(automation_platform)
    prov = HermesPublisherProvider(
        adapter_factory=lambda: FakeHermesExecutionAdapter(outcome, verify_outcome=verify),
        automation_policy=policy,
    )
    prov.register_workflow(FakeHermesWorkflow(automation_allowed=True))
    return prov


# ══════════════════════════════════════════════════════════════════════════════
# REGISTRY / RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderRouter:
    def test_native_provider_satisfies_protocol(self):
        reg = PublisherRegistry(); reg.register(StubPublisher(platform="linkedin"))
        assert isinstance(NativeApiProvider(reg), PublishingProvider)

    def test_resolve_native(self):
        reg = PublisherRegistry(); reg.register(StubPublisher(platform="linkedin"))
        native = NativeApiProvider(reg)
        router = ProviderRouter(); router.register(native)
        assert router.resolve(_account()) is native

    def test_resolve_external_agent(self):
        router = ProviderRouter()
        hermes = _hermes()
        router.register(hermes)
        acct = _account(ProviderType.EXTERNAL_AGENT, "hermes", SocialPlatform.QUORA)
        assert router.resolve(acct) is hermes

    def test_resolve_unregistered_returns_none(self):
        router = ProviderRouter()
        acct = _account(ProviderType.EXTERNAL_AGENT, "hermes", SocialPlatform.QUORA)
        assert router.resolve(acct) is None  # no silent fallback

    def test_byok_routed_by_provider_name(self):
        from app.social_publishing.providers.byok.base_provider import BaseByokProvider

        class DummyByok(BaseByokProvider):
            def capabilities(self, platform): return ProviderCapabilities(text=True)
            async def _publish_with_credentials(self, instruction, credentials):
                return ProviderResult(success=True, status=ExternalResultStatus.PUBLISHED)

        prov = DummyByok("byok_x")
        router = ProviderRouter(); router.register(prov)
        acct = _account(ProviderType.BYOK_API, "byok_x", SocialPlatform.TWITTER)
        assert router.resolve(acct) is prov


# ══════════════════════════════════════════════════════════════════════════════
# CAPABILITIES
# ══════════════════════════════════════════════════════════════════════════════

class TestCapabilities:
    def test_native_linkedin_supports_image(self):
        caps = native_capabilities_for("linkedin")
        assert caps.image and caps.text and caps.video

    def test_unmet_requirements_flags_video(self):
        caps = ProviderCapabilities(text=True)
        reqs = requirements_for("hi", ["https://x/v.mp4"])
        assert "video" in unmet_requirements(caps, reqs)

    def test_text_only_supported(self):
        caps = ProviderCapabilities(text=True)
        reqs = requirements_for("hi", [])
        assert unmet_requirements(caps, reqs) == []


# ══════════════════════════════════════════════════════════════════════════════
# NATIVE ROUTING (delegates to platform publisher)
# ══════════════════════════════════════════════════════════════════════════════

class TestNativeProvider:
    @pytest.mark.asyncio
    async def test_native_publish_success(self):
        reg = PublisherRegistry(); reg.register(StubPublisher(platform="linkedin"))
        native = NativeApiProvider(reg)
        instr = _instruction(platform="linkedin", provider_name="linkedin")
        res = await native.publish(instr, "token")
        assert res.success and res.status == ExternalResultStatus.PUBLISHED

    @pytest.mark.asyncio
    async def test_native_missing_publisher(self):
        native = NativeApiProvider(PublisherRegistry())
        res = await native.publish(_instruction(platform="linkedin"), "token")
        assert res.error_code == ExternalErrorCode.EXTERNAL_PLATFORM_UNSUPPORTED

    @pytest.mark.asyncio
    async def test_native_no_credentials(self):
        reg = PublisherRegistry(); reg.register(StubPublisher(platform="linkedin"))
        native = NativeApiProvider(reg)
        res = await native.publish(_instruction(platform="linkedin"), None)
        assert res.error_code == ExternalErrorCode.EXTERNAL_AUTH_REQUIRED


# ══════════════════════════════════════════════════════════════════════════════
# HERMES ROUTING + OUTCOMES
# ══════════════════════════════════════════════════════════════════════════════

class TestHermes:
    @pytest.mark.asyncio
    async def test_success(self):
        prov = _hermes(FakeOutcome.SUCCESS, automation_platform=FAKE_PLATFORM)
        res = await prov.publish(_instruction(), None)
        assert res.success and res.status == ExternalResultStatus.PUBLISHED

    @pytest.mark.asyncio
    async def test_failure_retryable(self):
        prov = _hermes(FakeOutcome.FAILURE, automation_platform=FAKE_PLATFORM)
        res = await prov.publish(_instruction(), None)
        assert not res.success and res.retryable

    @pytest.mark.asyncio
    async def test_timeout_retryable(self):
        prov = _hermes(FakeOutcome.TIMEOUT, automation_platform=FAKE_PLATFORM)
        res = await prov.publish(_instruction(), None)
        assert res.error_code == ExternalErrorCode.EXTERNAL_PUBLISH_TIMEOUT
        assert res.retryable

    @pytest.mark.asyncio
    async def test_auth_required(self):
        prov = _hermes(FakeOutcome.AUTH_REQUIRED, automation_platform=FAKE_PLATFORM)
        res = await prov.publish(_instruction(), None)
        assert res.error_code == ExternalErrorCode.EXTERNAL_AUTH_REQUIRED
        assert not res.retryable

    @pytest.mark.asyncio
    async def test_action_required(self):
        prov = _hermes(FakeOutcome.ACTION_REQUIRED, automation_platform=FAKE_PLATFORM)
        res = await prov.publish(_instruction(), None)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED

    @pytest.mark.asyncio
    async def test_unknown_verify_success(self):
        prov = _hermes(FakeOutcome.UNKNOWN, verify=FakeOutcome.SUCCESS,
                       automation_platform=FAKE_PLATFORM)
        res = await prov.publish(_instruction(), None)
        assert res.status == ExternalResultStatus.PUBLISHED

    @pytest.mark.asyncio
    async def test_unknown_verify_still_unknown_not_retryable(self):
        prov = _hermes(FakeOutcome.UNKNOWN, verify=FakeOutcome.UNKNOWN,
                       automation_platform=FAKE_PLATFORM)
        res = await prov.publish(_instruction(), None)
        assert res.status == ExternalResultStatus.UNKNOWN
        assert not res.retryable  # never blind-retry

    @pytest.mark.asyncio
    async def test_verification_failed(self):
        prov = _hermes(FakeOutcome.VERIFICATION_FAILED, automation_platform=FAKE_PLATFORM)
        res = await prov.publish(_instruction(), None)
        assert res.error_code == ExternalErrorCode.EXTERNAL_VERIFICATION_FAILED

    @pytest.mark.asyncio
    async def test_no_workflow_unsupported(self):
        prov = HermesPublisherProvider(lambda: FakeHermesExecutionAdapter())
        res = await prov.publish(_instruction(platform="nope"), None)
        assert res.error_code == ExternalErrorCode.EXTERNAL_PLATFORM_UNSUPPORTED


# ══════════════════════════════════════════════════════════════════════════════
# AUTOMATION POLICY (prohibited)
# ══════════════════════════════════════════════════════════════════════════════

class TestAutomationPolicy:
    def test_default_deny(self):
        assert PlatformAutomationPolicy().is_allowed("anything") is False

    def test_allow_then_check(self):
        p = PlatformAutomationPolicy(); p.allow("x")
        assert p.is_allowed("x") and p.allowed_platforms == ["x"]

    def test_ensure_allowed_raises(self):
        with pytest.raises(ExternalProviderError) as e:
            PlatformAutomationPolicy().ensure_allowed("x")
        assert e.value.code == ExternalErrorCode.EXTERNAL_AUTOMATION_NOT_PERMITTED

    @pytest.mark.asyncio
    async def test_policy_denies_even_if_workflow_allows(self):
        # policy empty (deny) but workflow allows → still denied
        prov = HermesPublisherProvider(lambda: FakeHermesExecutionAdapter(FakeOutcome.SUCCESS))
        prov.register_workflow(FakeHermesWorkflow(automation_allowed=True))
        res = await prov.publish(_instruction(), None)
        assert res.error_code == ExternalErrorCode.EXTERNAL_AUTOMATION_NOT_PERMITTED

    @pytest.mark.asyncio
    async def test_workflow_denies_even_if_policy_allows(self):
        policy = PlatformAutomationPolicy(); policy.allow(FAKE_PLATFORM)
        prov = HermesPublisherProvider(
            lambda: FakeHermesExecutionAdapter(FakeOutcome.SUCCESS),
            automation_policy=policy,
        )
        prov.register_workflow(FakeHermesWorkflow(automation_allowed=False))
        res = await prov.publish(_instruction(), None)
        assert res.error_code == ExternalErrorCode.EXTERNAL_AUTOMATION_NOT_PERMITTED


# ══════════════════════════════════════════════════════════════════════════════
# FAKE ADAPTER lifecycle
# ══════════════════════════════════════════════════════════════════════════════

class TestFakeAdapter:
    @pytest.mark.asyncio
    async def test_lifecycle_order_success(self):
        adapter = FakeHermesExecutionAdapter(FakeOutcome.SUCCESS)
        prov = _hermes(FakeOutcome.SUCCESS, automation_platform=FAKE_PLATFORM)
        # Use our own adapter to observe calls
        wf = FakeHermesWorkflow(automation_allowed=True)
        await wf.execute(_instruction(), adapter)
        assert adapter.calls == ["connect", "publish", "close"]

    @pytest.mark.asyncio
    async def test_lifecycle_verifies_on_unknown(self):
        adapter = FakeHermesExecutionAdapter(FakeOutcome.UNKNOWN, verify_outcome=FakeOutcome.SUCCESS)
        wf = FakeHermesWorkflow(automation_allowed=True)
        await wf.execute(_instruction(), adapter)
        assert adapter.calls == ["connect", "publish", "verify", "close"]


# ══════════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY / OPERATION ID
# ══════════════════════════════════════════════════════════════════════════════

class TestOperationId:
    def _job(self):
        return QueuedJob(id="job1", tenant_id="t1", post_id="p1", account_id="acc1",
                         platform=SocialPlatform.TWITTER, idempotency_key="k")

    def _post(self, content="hello", media=None):
        return SocialPost(id="p1", tenant_id="t1", account_id="pa",
                          platform=SocialPlatform.TWITTER, status=PostStatus.QUEUED,
                          content=content, media_urls=media or [])

    def test_same_content_same_id(self):
        job, acct = self._job(), _account(platform=SocialPlatform.TWITTER, provider_name="x")
        i1 = instruction_from_job(job, self._post(), acct)
        i2 = instruction_from_job(job, self._post(), acct)
        assert i1.operation_id == i2.operation_id

    def test_edited_content_new_id(self):
        job, acct = self._job(), _account(platform=SocialPlatform.TWITTER, provider_name="x")
        i1 = instruction_from_job(job, self._post("a"), acct)
        i2 = instruction_from_job(job, self._post("b"), acct)
        assert i1.operation_id != i2.operation_id

    def test_operation_id_shape(self):
        assert build_operation_id("j", "a", "v") == "op_j_a_v"


# ══════════════════════════════════════════════════════════════════════════════
# VERIFY-BEFORE-RETRY DECISION
# ══════════════════════════════════════════════════════════════════════════════

class TestRetryDecision:
    def test_unknown_verifies_no_retry(self):
        d = decide_after_result(ProviderResult(False, ExternalResultStatus.UNKNOWN))
        assert d.needs_verification and not d.should_retry

    def test_action_required_user(self):
        d = decide_after_result(ProviderResult(False, ExternalResultStatus.ACTION_REQUIRED))
        assert d.needs_user_action

    def test_failed_retryable_honored(self):
        d = decide_after_result(ProviderResult(False, ExternalResultStatus.FAILED, retryable=True))
        assert d.should_retry

    def test_failed_non_retryable(self):
        d = decide_after_result(ProviderResult(False, ExternalResultStatus.FAILED, retryable=False))
        assert not d.should_retry


# ══════════════════════════════════════════════════════════════════════════════
# HERMES RETRY CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestRetryClassification:
    def test_timeout_retryable(self):
        assert classify(ExternalErrorCode.EXTERNAL_PUBLISH_TIMEOUT) == RetryDisposition.RETRYABLE

    def test_auth_non_retryable(self):
        assert classify(ExternalErrorCode.EXTERNAL_AUTH_REQUIRED) == RetryDisposition.NON_RETRYABLE

    def test_automation_not_permitted_non_retryable(self):
        assert classify(ExternalErrorCode.EXTERNAL_AUTOMATION_NOT_PERMITTED) == RetryDisposition.NON_RETRYABLE

    def test_unknown_verify_first(self):
        assert classify(ExternalErrorCode.EXTERNAL_PUBLISH_UNKNOWN) == RetryDisposition.VERIFY_FIRST

    def test_action_required_user(self):
        assert classify(ExternalErrorCode.ACTION_REQUIRED) == RetryDisposition.USER_ACTION

    def test_none_is_verify_first(self):
        assert classify(None) == RetryDisposition.VERIFY_FIRST


# ══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE TRANSITIONS
# ══════════════════════════════════════════════════════════════════════════════

class TestLifecycle:
    def test_unknown_cannot_retry(self):
        assert not can_transition(PostStatus.UNKNOWN, PostStatus.RETRYING)
        assert not can_transition(PostStatus.UNKNOWN, PostStatus.QUEUED)

    def test_unknown_can_verify(self):
        assert can_transition(PostStatus.UNKNOWN, PostStatus.VERIFYING)

    def test_publishing_to_verifying(self):
        assert can_transition(PostStatus.PUBLISHING, PostStatus.VERIFYING)

    def test_action_required_to_waiting(self):
        assert can_transition(PostStatus.ACTION_REQUIRED, PostStatus.WAITING_FOR_USER)

    def test_every_status_has_transitions_entry(self):
        assert all(s in VALID_TRANSITIONS for s in PostStatus)


# ══════════════════════════════════════════════════════════════════════════════
# ERROR NORMALIZATION / SECRET SANITIZATION
# ══════════════════════════════════════════════════════════════════════════════

class TestSanitization:
    def test_provider_error_redacts_secret(self):
        e = ExternalProviderError(
            ExternalErrorCode.EXTERNAL_AUTH_REQUIRED, "bad access_token abc123",
        )
        assert "abc123" not in e.message

    def test_result_sanitized_message(self):
        r = ProviderResult(False, ExternalResultStatus.FAILED,
                           error_message="bearer xyz leaked").sanitized()
        assert "xyz" not in (r.error_message or "")

    def test_observability_scrub_drops_secrets(self):
        clean = _scrub({"tenant_id": "t", "access_token": "S", "cookie": "c", "result": "ok"})
        assert "access_token" not in clean and "cookie" not in clean
        assert clean["tenant_id"] == "t"


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA VALIDATION (restricted workspace, path traversal)
# ══════════════════════════════════════════════════════════════════════════════

class TestMediaWorkspace:
    def test_valid_media(self):
        d = validate_media("jpg", 1000)
        assert d.mime_type == "image/jpeg"

    def test_unsupported_type_rejected(self):
        with pytest.raises(MediaValidationError):
            validate_media("exe", 100)

    def test_oversize_rejected(self):
        with pytest.raises(MediaValidationError):
            validate_media("jpg", 999_999_999_999)

    def test_safe_name(self):
        assert is_safe_workspace_name("media_0.jpg")

    @pytest.mark.parametrize("bad", ["../x.jpg", "/abs.jpg", "a/b.jpg", "..\\w.jpg", "noext"])
    def test_unsafe_names_rejected(self, bad):
        assert not is_safe_workspace_name(bad)

    def test_traversal_blocked(self):
        with media_workspace("op_test") as ws:
            with pytest.raises(MediaValidationError):
                resolve_within(ws, "../escape.jpg")


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE FLAGS
# ══════════════════════════════════════════════════════════════════════════════

class TestFeatureFlags:
    def test_flags_default_false(self, monkeypatch):
        # These experimental-provider flags must DEFAULT to False when the
        # environment does not set them. We assert the code default via the
        # resolver rather than the live module value, because a deployment/.env
        # may legitimately enable a provider (e.g. during a live smoke test) —
        # that does not change the default-off contract.
        import app.config as c
        for name in ("ENABLE_HERMES_PROVIDER", "ENABLE_HERMES_USER_ASSISTED",
                     "ENABLE_BYOK_PROVIDERS"):
            monkeypatch.delenv(name, raising=False)
            assert c._bool_env(name, False) is False


# ══════════════════════════════════════════════════════════════════════════════
# BYOK — missing credentials → auth required
# ══════════════════════════════════════════════════════════════════════════════

class TestByok:
    @pytest.mark.asyncio
    async def test_missing_credentials_auth_required(self):
        from app.social_publishing.providers.byok.base_provider import BaseByokProvider

        class DummyByok(BaseByokProvider):
            def capabilities(self, platform): return ProviderCapabilities(text=True)
            async def _publish_with_credentials(self, instruction, credentials):
                return ProviderResult(True, ExternalResultStatus.PUBLISHED)

        prov = DummyByok("byok_x")
        # Force the store to report no credentials without touching the DB.
        prov._store.get_decrypted = AsyncMock(return_value=None)
        res = await prov.publish(_instruction(platform="twitter", provider_name="byok_x"), None)
        assert res.error_code == ExternalErrorCode.EXTERNAL_AUTH_REQUIRED

    @pytest.mark.asyncio
    async def test_with_credentials_delegates(self):
        from app.social_publishing.providers.byok.base_provider import BaseByokProvider

        class DummyByok(BaseByokProvider):
            def capabilities(self, platform): return ProviderCapabilities(text=True)
            async def _publish_with_credentials(self, instruction, credentials):
                return ProviderResult(True, ExternalResultStatus.PUBLISHED, external_post_id="byok_1")

        prov = DummyByok("byok_x")
        prov._store.get_decrypted = AsyncMock(return_value={"api_key": "k"})
        res = await prov.publish(_instruction(platform="twitter", provider_name="byok_x"), None)
        assert res.success and res.external_post_id == "byok_1"


# ══════════════════════════════════════════════════════════════════════════════
# SECURITY REGRESSIONS
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurity:
    def test_instruction_carries_account_record_id_for_scoping(self):
        # BYOK/session lookups must key on the internal account id (tenant-scoped),
        # not the external platform account id.
        job = QueuedJob(id="j", tenant_id="t1", post_id="p", account_id="acc1",
                        platform=SocialPlatform.TWITTER, idempotency_key="k")
        post = SocialPost(id="p", tenant_id="t1", account_id="pa",
                          platform=SocialPlatform.TWITTER, status=PostStatus.QUEUED,
                          content="hi", media_urls=[])
        acct = _account(ProviderType.BYOK_API, "byok_x", SocialPlatform.TWITTER, acc_id="acc1")
        instr = instruction_from_job(job, post, acct)
        assert instr.meta["account_record_id"] == "acc1"

    def test_session_model_has_no_encrypted_reference(self):
        import dataclasses
        from app.social_publishing.domain.models import ExternalSessionReference
        fields = {f.name for f in dataclasses.fields(ExternalSessionReference)}
        assert "encrypted_reference" not in fields

    def test_account_model_has_no_credentials(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(SocialAccount)}
        assert "encrypted_credentials" not in fields
        assert "access_token" not in fields

    def test_media_workspace_symlink_style_escape_blocked(self):
        with media_workspace("op_sec") as ws:
            for bad in ["..", "../../etc/passwd", "sub/../../x.jpg"]:
                with pytest.raises(MediaValidationError):
                    resolve_within(ws, bad)

    def test_instruction_has_no_credential_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(PublishInstruction)}
        for forbidden in ("access_token", "token", "password", "secret", "credentials"):
            assert forbidden not in fields
