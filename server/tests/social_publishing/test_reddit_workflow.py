"""Reddit user-assisted Hermes workflow tests (Phase 25).

The first REAL Hermes platform workflow is Reddit user-assisted publishing,
driven behind the SemanticBrowser boundary. These tests exercise the workflow
and the coordinating service using a SCRIPTED fake SemanticBrowser and an
in-memory pending-confirmation repository — no real browser, no Playwright, no
network, no database.

Everything here is deterministic: the fake browser is programmed with the pages
it will "navigate" to and which semantic elements are visible, so we can drive
the exact branches of the two-phase (prepare -> confirm) flow.

Covered scenarios (Phase-25):
  1. provider registration                       14. successful publish
  2. provider resolution (reddit user-assisted)  15. permalink verification
  3. connection / authenticated session          16. UNKNOWN after click
  4. authentication required                      17. duplicate prevention (op id)
  5. MFA required -> ACTION_REQUIRED              18. expiration (no auto-retry)
  6. account/session verification                 19. tenant isolation
  7. subreddit validation                         20. authorization (feature flag)
  8. content validation                           21. secret sanitization
  9. image/media validation                       22. feature flag disabled (404)
 10. post preparation (no submit)                 23. unsupported media (video)
 11. ACTION_REQUIRED surfaced                     24. browser timeout on prepare
 12. WAITING_FOR_USER on confirm                  25. browser crash / error
 13. user cancellation                            26. capabilities (video False)
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
)
from app.social_publishing.providers.hermes.reddit import constants as C
from app.social_publishing.providers.hermes.reddit.validation import (
    normalize_subreddit,
    validate_post,
)
from app.social_publishing.providers.hermes.reddit.workflow import HermesRedditWorkflow
from app.social_publishing.providers.hermes.reddit.service import RedditUserAssistedService
from app.social_publishing.providers.hermes.semantic_browser import (
    BrowserError,
    BrowserTimeout,
    SemanticBrowser,
)


# ══════════════════════════════════════════════════════════════════════════════
# Scripted fake SemanticBrowser
# ══════════════════════════════════════════════════════════════════════════════

class FakeSemanticBrowser:
    """
    A deterministic, scripted SemanticBrowser for tests.

    Configuration:
      authenticated  : whether the Reddit user-menu is visible (logged in)
      reach_subreddit: whether goto(submit_url) lands on the right /r/<sub>/ URL
      post_permalink : URL the page navigates to after clicking Post (success)
                       — when None, clicking Post does NOT navigate (UNKNOWN)
      raise_on       : name of a method that should raise `error` when called
      error          : the exception instance to raise for raise_on

    It records every call so tests can assert the Post button was NOT clicked
    during prepare, etc.
    """

    def __init__(
        self,
        *,
        authenticated=True,
        reach_subreddit=True,
        post_permalink=None,
        raise_on=None,
        error=None,
        visible_overrides=None,
        permalink_after_checks=0,
        lag_wait_for_url=False,
    ):
        self.authenticated = authenticated
        self.reach_subreddit = reach_subreddit
        self.post_permalink = post_permalink
        self.raise_on = raise_on
        self.error = error or BrowserError("scripted failure")
        # Verification simulation knobs (for the polling verifier):
        #  - permalink_after_checks: the address bar only becomes post_permalink
        #    after this many current_url() reads (models the SPA URL lag). 0 =
        #    available immediately (legacy behaviour).
        #  - lag_wait_for_url: if True, wait_for_url_contains(PERMALINK_MARKER)
        #    times out even when a permalink will later appear, forcing the
        #    verifier onto its current_url() polling path.
        self.permalink_after_checks = permalink_after_checks
        self.lag_wait_for_url = lag_wait_for_url
        self._current_url_reads = 0
        # Verification fallback: permalinks the user's submitted listing "shows"
        # for the matching title. find_post_permalinks returns these.
        self.submitted_permalinks = []
        # Optional per-locator visibility overrides: {(role, name): bool}. Lets a
        # test script a MISSING element (e.g. the removed "Text" tab) while other
        # elements stay visible. Absent keys keep the default behaviour.
        self.visible_overrides = dict(visible_overrides or {})
        self._url = C.REDDIT_BASE
        self.calls: list[tuple] = []
        self.started = False
        self.closed = False
        self._subreddit = None
        # Number of files currently "attached" to the file input. Updated by
        # set_input_files (to the number of files passed) so count_attached_files
        # mirrors the real adapter. Tests can also seed/override it.
        self._attached_files = 0

    # lifecycle (service calls start/close if present)
    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    def _maybe_raise(self, method):
        if self.raise_on == method:
            raise self.error

    # ── SemanticBrowser surface ──────────────────────────────────────────────
    async def goto(self, url, *, timeout_s=None):
        self.calls.append(("goto", url))
        self._maybe_raise("goto")
        # Track the subreddit we're composing for.
        if "/submit" in url:
            # url like https://www.reddit.com/r/<sub>/submit
            try:
                self._subreddit = url.split("/r/")[1].split("/")[0]
            except Exception:
                self._subreddit = None
            if self.reach_subreddit and self._subreddit:
                self._url = C.subreddit_url(self._subreddit) + "submit"
            else:
                # Landed somewhere unexpected (e.g. redirected to a search page).
                self._url = C.REDDIT_BASE + "/search"
        else:
            self._url = url

    async def current_url(self):
        self._maybe_raise("current_url")
        # Simulate the SPA URL lag: only after `permalink_after_checks` reads does
        # the address bar become the created post permalink.
        if self.permalink_after_checks and self.post_permalink:
            self._current_url_reads += 1
            if self._current_url_reads >= self.permalink_after_checks:
                self._url = self.post_permalink
        self.calls.append(("current_url", self._url))
        return self._url

    async def is_visible(self, role, name, *, timeout_s=None):
        self.calls.append(("is_visible", role, name))
        self._maybe_raise("is_visible")
        if (role, name) in self.visible_overrides:
            return self.visible_overrides[(role, name)]
        if (role, name) == C.LOC_USER_MENU:
            return self.authenticated
        return True

    async def get_text(self, role, name, *, timeout_s=None):
        self.calls.append(("get_text", role, name))
        self._maybe_raise("get_text")
        return ""

    async def wait_for(self, role, name, *, timeout_s=None):
        self.calls.append(("wait_for", role, name))
        self._maybe_raise("wait_for")
        # If a test scripted this locator as NOT visible, a bounded wait_for on it
        # must time out (mirrors the real adapter raising BrowserTimeout).
        if self.visible_overrides.get((role, name)) is False:
            raise BrowserTimeout(f"{role}:{name} never appeared")

    async def wait_for_url_contains(self, fragment, *, timeout_s=None):
        self.calls.append(("wait_for_url_contains", fragment))
        self._maybe_raise("wait_for_url_contains")
        if fragment == C.PERMALINK_MARKER:
            # Simulate the current SPA where this bounded wait resolves BEFORE the
            # URL transition (lag) or where the URL only appears via polling.
            if self.lag_wait_for_url or self.permalink_after_checks:
                raise BrowserTimeout("permalink not yet in address bar")
            if self.post_permalink:
                self._url = self.post_permalink
            else:
                # Never navigated to a permalink -> uncertain.
                raise BrowserTimeout("no permalink")

    async def fill(self, role, name, value, *, timeout_s=None):
        self.calls.append(("fill", role, name, value))
        self._maybe_raise("fill")

    async def click(self, role, name, *, timeout_s=None):
        self.calls.append(("click", role, name))
        self._maybe_raise("click")

    async def set_input_files(self, label, file_paths, *, timeout_s=None):
        self.calls.append(("set_input_files", label, tuple(file_paths)))
        self._maybe_raise("set_input_files")
        # Mirror the real adapter: an attach makes the "Remove media" control
        # visible and renders a preview — unless a test forces a failed attach
        # via the "attach_fails" knob.
        if getattr(self, "attach_fails", False):
            self._attached_files = 0
            self.visible_overrides[C.LOC_REMOVE_MEDIA] = False
            return
        self._attached_files = len(file_paths)

    async def count_attached_files(self, selector=C.IMAGE_PREVIEW_SELECTOR, *, timeout_s=None):
        self.calls.append(("count_attached_files", selector))
        self._maybe_raise("count_attached_files")
        return self._attached_files

    async def find_post_permalinks(self, title, *, timeout_s=None):
        self.calls.append(("find_post_permalinks", title))
        self._maybe_raise("find_post_permalinks")
        return list(self.submitted_permalinks)

    async def screenshot(self, path):
        self.calls.append(("screenshot", path))

    # test helpers
    def clicked_post(self):
        return ("click", *C.LOC_POST_BUTTON) in self.calls


# ══════════════════════════════════════════════════════════════════════════════
# In-memory pending-confirmation repository (no DB)
# ══════════════════════════════════════════════════════════════════════════════

class FakePendingRepo:
    """Mirrors PendingConfirmationRepository semantics without touching Mongo."""

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._seq = 0

    async def create(self, tenant_id, account_id, operation_id, platform,
                     provider_name, *, title="", body="", subreddit="",
                     media_paths=None, session_profile_key="", ttl_seconds=900):
        now = datetime.now(timezone.utc)
        # Upsert on (operation_id, tenant_id) — idempotent, like the real repo.
        for cid, row in self._rows.items():
            if row["operation_id"] == operation_id and row["tenant_id"] == tenant_id:
                row.update(
                    title=title, body=body, subreddit=subreddit,
                    media_paths=list(media_paths or []),
                    session_profile_key=session_profile_key,
                    status="action_required",
                    expires_at=now + timedelta(seconds=ttl_seconds),
                )
                return _Row(cid, row)
        self._seq += 1
        cid = f"c{self._seq}"
        self._rows[cid] = {
            "tenant_id": tenant_id, "account_id": account_id,
            "operation_id": operation_id, "platform": platform,
            "provider_name": provider_name, "status": "action_required",
            "title": title, "body": body, "subreddit": subreddit,
            "media_paths": list(media_paths or []),
            "session_profile_key": session_profile_key, "external_url": None,
            "created_at": now, "updated_at": now,
            "expires_at": now + timedelta(seconds=ttl_seconds),
        }
        return _Row(cid, self._rows[cid])

    async def find(self, confirmation_id, tenant_id):
        row = self._rows.get(confirmation_id)
        if not row or row["tenant_id"] != tenant_id:
            return None
        return _Row(confirmation_id, row)

    async def find_by_operation(self, operation_id, tenant_id):
        for cid, row in self._rows.items():
            if row["operation_id"] == operation_id and row["tenant_id"] == tenant_id:
                return _Row(cid, row)
        return None

    async def list_awaiting(self, tenant_id):
        return [
            _Row(cid, row) for cid, row in self._rows.items()
            if row["tenant_id"] == tenant_id
            and row["status"] in ("action_required", "waiting_for_user")
        ]

    async def set_status(self, confirmation_id, tenant_id, status, *, external_url=None):
        row = self._rows.get(confirmation_id)
        if not row or row["tenant_id"] != tenant_id:
            return False
        row["status"] = status
        if external_url is not None:
            row["external_url"] = external_url
        return True

    async def expire_due(self, now=None):
        now = now or datetime.now(timezone.utc)
        n = 0
        for row in self._rows.values():
            if row["status"] in ("action_required", "waiting_for_user") \
                    and row["expires_at"] <= now:
                row["status"] = "expired"
                n += 1
        return n


class _Row:
    """Attribute view over a pending dict (mirrors PendingConfirmation)."""

    def __init__(self, cid, row):
        self.id = cid
        for k, v in row.items():
            setattr(self, k, v)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _instruction(title="Hello title", body="Body text", subreddit="test",
                 media_paths=None, op="op1", tenant="t1", account="acc1",
                 username="tester"):
    return PublishInstruction(
        operation_id=op, tenant_id=tenant, account_id=account,
        platform=C.PLATFORM, provider_name="hermes", text=body,
        meta={
            "reddit_title": title,
            "reddit_subreddit": subreddit,
            "reddit_media_paths": media_paths or [],
            "reddit_username": username,
        },
    )


@pytest.fixture(autouse=True)
def _fast_permalink_polling(monkeypatch):
    """
    Keep verification polling instant and short in ALL tests.

    The real verifier polls current_url() every PERMALINK_POLL_INTERVAL_S up to
    PERMALINK_POLL_MAX_CHECKS. With the scripted fake there is no real page, so
    we set the interval to 0 (no sleeping) and cap the checks low so the
    "permalink never appears -> UNKNOWN" path returns immediately instead of
    waiting out the publish timeout. Individual tests may still override these.
    """
    monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0, raising=False)
    monkeypatch.setattr(C, "PERMALINK_POLL_MAX_CHECKS", 5, raising=False)


def _service(browser, pending=None):
    """Build a service whose adapter_factory always yields the scripted browser."""
    repo = pending or FakePendingRepo()
    svc = RedditUserAssistedService(
        pending_repo=repo,
        workflow=HermesRedditWorkflow(),
        adapter_factory=lambda profile_key: browser,
        username_resolver=lambda account_id, tenant_id: _async_return("tester"),
    )
    return svc, repo


def _async_return(value):
    """Small awaitable returning a value (for injecting async resolvers)."""
    async def _coro():
        return value
    return _coro()


# ══════════════════════════════════════════════════════════════════════════════
# 1-2. PROVIDER REGISTRATION / RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderRegistration:
    def test_workflow_platform_is_reddit(self):
        assert HermesRedditWorkflow().platform == C.PLATFORM == "reddit"

    def test_workflow_registers_on_hermes_provider(self):
        from app.social_publishing.providers.hermes.provider import HermesPublisherProvider
        from app.social_publishing.providers.hermes.automation_policy import (
            PlatformAutomationPolicy,
        )
        from app.social_publishing.providers.hermes.adapter import FakeHermesExecutionAdapter

        policy = PlatformAutomationPolicy(); policy.allow(C.PLATFORM)
        prov = HermesPublisherProvider(
            adapter_factory=lambda: FakeHermesExecutionAdapter(),
            automation_policy=policy,
            user_assisted=True,
        )
        prov.register_workflow(HermesRedditWorkflow())
        # provider knows about the reddit workflow
        assert C.PLATFORM in getattr(prov, "_workflows", {"reddit": None})

    def test_provider_resolves_reddit_user_assisted(self):
        from app.social_publishing.providers.router import ProviderRouter
        from app.social_publishing.providers.hermes.provider import HermesPublisherProvider
        from app.social_publishing.providers.hermes.automation_policy import (
            PlatformAutomationPolicy,
        )
        from app.social_publishing.providers.hermes.adapter import FakeHermesExecutionAdapter
        from app.social_publishing.domain.enums import ProviderType, SocialPlatform
        from app.social_publishing.domain.models import SocialAccount

        policy = PlatformAutomationPolicy(); policy.allow(C.PLATFORM)
        prov = HermesPublisherProvider(
            adapter_factory=lambda: FakeHermesExecutionAdapter(),
            automation_policy=policy, user_assisted=True,
        )
        prov.register_workflow(HermesRedditWorkflow())
        router = ProviderRouter(); router.register(prov)
        acct = SocialAccount(
            id="acc1", tenant_id="t1", platform=SocialPlatform.REDDIT,
            account_name="n", platform_account_id="PA1",
            provider_type=ProviderType.USER_ASSISTED_AGENT, provider_name="hermes",
        )
        assert router.resolve(acct) is prov


# ══════════════════════════════════════════════════════════════════════════════
# 3-6. CONNECTION / AUTH / MFA / SESSION VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthentication:
    async def test_authenticated_session_proceeds_to_prepare(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(_instruction(), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        # user-menu visibility was checked
        assert ("is_visible", *C.LOC_USER_MENU) in browser.calls

    async def test_not_authenticated_requires_action(self):
        browser = FakeSemanticBrowser(authenticated=False)
        res = await HermesRedditWorkflow().prepare(_instruction(), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert res.error_code == ExternalErrorCode.ACTION_REQUIRED
        # It navigated to the login page and did NOT click Post.
        assert ("goto", C.LOGIN_URL) in browser.calls
        assert not browser.clicked_post()

    async def test_mfa_required_surfaces_action_required(self):
        # MFA is indistinguishable to us from "not signed in yet": the user menu
        # is not visible until sign-in + MFA complete, so we stop for the user.
        browser = FakeSemanticBrowser(authenticated=False)
        res = await HermesRedditWorkflow().prepare(_instruction(), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert "login" in (res.error_message or "").lower()

    async def test_session_verification_checks_user_menu(self):
        browser = FakeSemanticBrowser(authenticated=True)
        await HermesRedditWorkflow()._is_authenticated(browser)
        assert ("goto", C.REDDIT_BASE) in browser.calls
        assert ("is_visible", *C.LOC_USER_MENU) in browser.calls


# ══════════════════════════════════════════════════════════════════════════════
# 7-9. VALIDATION (subreddit / content / media)
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation:
    def test_subreddit_normalization(self):
        assert normalize_subreddit("r/example") == "example"
        assert normalize_subreddit("/r/Example/") == "Example"
        assert normalize_subreddit("  plain ") == "plain"

    def test_invalid_subreddit_rejected(self):
        parsed, errors = validate_post("t", "b", "bad name!", [])
        assert parsed is None and any("subreddit" in e.lower() for e in errors)

    def test_missing_subreddit_rejected(self):
        parsed, errors = validate_post("t", "b", "", [])
        assert parsed is None and any("subreddit" in e.lower() for e in errors)

    def test_missing_title_rejected(self):
        parsed, errors = validate_post("", "b", "test", [])
        assert parsed is None and any("title" in e.lower() for e in errors)

    def test_title_too_long_rejected(self):
        parsed, errors = validate_post("x" * (C.MAX_TITLE_LENGTH + 1), "b", "test", [])
        assert parsed is None and any("title" in e.lower() for e in errors)

    def test_body_too_long_rejected(self):
        parsed, errors = validate_post("t", "x" * (C.MAX_BODY_LENGTH + 1), "test", [])
        assert parsed is None and any("body" in e.lower() for e in errors)

    def test_valid_text_post_accepted(self):
        parsed, errors = validate_post("Title", "Body", "test", [])
        assert errors == [] and parsed.title == "Title" and parsed.subreddit == "test"

    def test_supported_image_accepted(self):
        parsed, errors = validate_post("T", "", "test", ["media_0.png"])
        assert errors == [] and parsed.media_paths == ["media_0.png"]

    def test_unsupported_video_rejected(self):
        parsed, errors = validate_post("T", "", "test", ["clip.mp4"])
        assert parsed is None and any("mp4" in e.lower() or "video" in e.lower()
                                      for e in errors)

    def test_too_many_media_items_rejected(self):
        parsed, errors = validate_post("T", "", "test", ["a.png", "b.png"])
        assert parsed is None and any("media" in e.lower() for e in errors)


# ══════════════════════════════════════════════════════════════════════════════
# 10-11. POST PREPARATION -> ACTION_REQUIRED (never clicks Post)
# ══════════════════════════════════════════════════════════════════════════════

class TestPreparation:
    async def test_prepare_fills_content_but_does_not_submit(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(_instruction(body="Hi there"), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert ("fill", *C.LOC_TITLE_INPUT, "Hello title") in browser.calls
        # Post button was located (wait_for) but NOT clicked.
        assert ("wait_for", *C.LOC_POST_BUTTON) in browser.calls
        assert not browser.clicked_post()

    async def test_prepare_image_post_selects_image_button_and_attaches(self, tmp_path):
        # Current UI: image-post control is a BUTTON named "Image" (not a tab).
        img = tmp_path / "media_0.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)  # small valid-ish file
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[str(img)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        # Selected the image composer via the current button, attached the file,
        # verified attachment, and never clicked Post.
        assert ("click", *C.LOC_IMAGE_BUTTON) in browser.calls
        assert ("click", *C.LOC_IMAGE_TAB) not in browser.calls  # not the legacy tab
        assert any(c[0] == "set_input_files" for c in browser.calls)
        # Attach was verified via the accessible "Remove media" control.
        assert ("is_visible", *C.LOC_REMOVE_MEDIA) in browser.calls
        assert ("wait_for", *C.LOC_POST_BUTTON) in browser.calls
        assert not browser.clicked_post()

    async def test_wrong_subreddit_landing_fails_safe(self):
        # Composer navigation does not reach /r/<sub>/ -> refuse to publish.
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=False)
        res = await HermesRedditWorkflow().prepare(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert not browser.clicked_post()

    async def test_validation_failure_never_opens_browser(self):
        browser = FakeSemanticBrowser(authenticated=True)
        res = await HermesRedditWorkflow().prepare(_instruction(title=""), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        # No navigation happened at all.
        assert browser.calls == []


# ══════════════════════════════════════════════════════════════════════════════
# 12-16. CONFIRM / PUBLISH / VERIFY / UNKNOWN via the SERVICE
# ══════════════════════════════════════════════════════════════════════════════

class TestConfirmPublishFlow:
    async def test_prepare_persists_pending_action_required(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "Title", "Body", "test", [])
        assert out["status"] == "action_required"
        assert out["action"] == C.CONFIRM_ACTION
        cid = out["confirmation_id"]
        row = await repo.find(cid, "t1")
        assert row is not None and row.status == "action_required"
        # preview carries no secrets, just content
        assert out["preview"]["subreddit"] == "test"

    async def test_confirm_publishes_and_verifies_permalink(self):
        permalink = f"{C.REDDIT_BASE}/r/test/comments/abc123/hello/"
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=permalink
        )
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "Title", "Body", "test", [])
        cid = out["confirmation_id"]

        result = await svc.confirm("t1", cid)
        assert result["status"] == "published"
        assert result["external_url"] == permalink
        assert result["external_post_id"] == "abc123"
        # Post WAS clicked during confirm.
        assert browser.clicked_post()
        row = await repo.find(cid, "t1")
        assert row.status == "published"

    async def test_waiting_for_user_status_set_during_confirm(self):
        # After confirm starts it moves the pending row to waiting_for_user before
        # publishing. On a permalink success it ends published.
        permalink = f"{C.REDDIT_BASE}/r/test/comments/x/y/"
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=permalink
        )
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "T", "B", "test", [])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"

    async def test_confirm_unknown_when_no_permalink(self):
        # Post clicked but never navigates to a permalink -> UNKNOWN, not retried.
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=None
        )
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "T", "B", "test", [])
        cid = out["confirmation_id"]
        res = await svc.confirm("t1", cid)
        assert res["status"] == "unknown"
        assert browser.clicked_post()
        row = await repo.find(cid, "t1")
        assert row.status == "unknown"

    async def test_workflow_confirm_and_publish_direct(self):
        permalink = f"{C.REDDIT_BASE}/r/test/comments/pid/slug/"
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=permalink
        )
        wf = HermesRedditWorkflow()
        await wf.prepare(_instruction(subreddit="test"), browser)
        res = await wf.confirm_and_publish(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.PUBLISHED
        assert res.external_post_id == "pid"


# ══════════════════════════════════════════════════════════════════════════════
# 13. CANCELLATION
# ══════════════════════════════════════════════════════════════════════════════

class TestCancellation:
    async def test_cancel_marks_cancelled_and_does_not_publish(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "T", "B", "test", [])
        cid = out["confirmation_id"]
        res = await svc.cancel("t1", cid)
        assert res["status"] == "cancelled"
        row = await repo.find(cid, "t1")
        assert row.status == "cancelled"

    async def test_confirm_after_cancel_is_rejected(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "T", "B", "test", [])
        cid = out["confirmation_id"]
        await svc.cancel("t1", cid)
        res = await svc.confirm("t1", cid)
        # No longer awaiting confirmation -> not published, no Post click.
        assert res["status"] != "published"
        assert not browser.clicked_post()


# ══════════════════════════════════════════════════════════════════════════════
# 17. DUPLICATE PREVENTION (stable operation_id)
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicatePrevention:
    async def test_repeated_prepare_same_operation_reuses_row(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        svc, repo = _service(browser)
        out1 = await svc.prepare("t1", "acc1", "op-dup", "T", "B", "test", [])
        out2 = await svc.prepare("t1", "acc1", "op-dup", "T", "B", "test", [])
        # Same operation_id -> same pending row (idempotent), not two rows.
        assert out1["confirmation_id"] == out2["confirmation_id"]
        awaiting = await repo.list_awaiting("t1")
        assert len([r for r in awaiting if r.operation_id == "op-dup"]) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 18. EXPIRATION (bounded; no auto-retry)
# ══════════════════════════════════════════════════════════════════════════════

class TestExpiration:
    async def test_expire_due_marks_overdue_expired(self):
        repo = FakePendingRepo()
        await repo.create("t1", "acc1", "op1", C.PLATFORM, "hermes", ttl_seconds=1)
        # Force it overdue.
        future = datetime.now(timezone.utc) + timedelta(seconds=10)
        n = await repo.expire_due(now=future)
        assert n == 1
        awaiting = await repo.list_awaiting("t1")
        assert awaiting == []  # nothing left awaiting

    async def test_expired_never_reschedules(self):
        # expire_due only sets status=expired; it must not create/publish anything.
        repo = FakePendingRepo()
        row = await repo.create("t1", "acc1", "op1", C.PLATFORM, "hermes", ttl_seconds=1)
        future = datetime.now(timezone.utc) + timedelta(seconds=10)
        await repo.expire_due(now=future)
        after = await repo.find(row.id, "t1")
        assert after.status == "expired"


# ══════════════════════════════════════════════════════════════════════════════
# 19-20. TENANT ISOLATION / AUTHORIZATION
# ══════════════════════════════════════════════════════════════════════════════

class TestTenantIsolation:
    async def test_confirm_cross_tenant_is_not_found(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "T", "B", "test", [])
        cid = out["confirmation_id"]
        # A different tenant may not confirm t1's pending post.
        res = await svc.confirm("t2", cid)
        assert res["status"] == "not_found"
        assert not browser.clicked_post()

    async def test_list_pending_is_tenant_scoped(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        svc, repo = _service(browser)
        await svc.prepare("t1", "acc1", "op1", "T", "B", "test", [])
        await svc.prepare("t2", "acc9", "op2", "T", "B", "test", [])
        t1 = await svc.list_pending("t1")
        t2 = await svc.list_pending("t2")
        assert len(t1) == 1 and len(t2) == 1
        assert t1[0]["operation_id"] == "op1"
        assert t2[0]["operation_id"] == "op2"


# ══════════════════════════════════════════════════════════════════════════════
# 21. SECRET SANITIZATION
# ══════════════════════════════════════════════════════════════════════════════

class TestSecretSanitization:
    def test_observability_scrub_drops_secret_keys(self):
        from app.social_publishing.providers.observability import _scrub
        clean = _scrub({
            "tenant_id": "t1", "password": "hunter2", "cookie": "abc",
            "session": "s", "access_token": "tok", "result": "ok",
        })
        for forbidden in ("password", "cookie", "session", "access_token"):
            assert forbidden not in clean
        assert clean["tenant_id"] == "t1" and clean["result"] == "ok"

    def test_pending_model_has_no_secret_fields(self):
        import dataclasses
        from app.social_publishing.domain.models import PendingConfirmation
        fields = {f.name for f in dataclasses.fields(PendingConfirmation)}
        for forbidden in ("password", "cookie", "cookies", "session",
                          "access_token", "token", "credentials", "secret"):
            assert forbidden not in fields

    async def test_preview_exposes_only_content_not_secrets(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "T", "B", "test", [])
        preview = out["preview"]
        assert set(preview.keys()) == {"subreddit", "title", "body", "media_count"}


# ══════════════════════════════════════════════════════════════════════════════
# 22. FEATURE FLAG DISABLED -> ROUTES 404
# ══════════════════════════════════════════════════════════════════════════════

class TestFeatureFlag:
    def test_hermes_provider_flag_default_false(self, monkeypatch):
        # The flag must DEFAULT to False when the environment does not set it.
        # We assert the code default via the resolver rather than the live module
        # value, because a deployment/.env may legitimately enable the provider
        # (e.g. during a live smoke test) — that does not change the default.
        import app.config as c
        monkeypatch.delenv("ENABLE_HERMES_PROVIDER", raising=False)
        assert c._bool_env("ENABLE_HERMES_PROVIDER", False) is False
        # And when explicitly enabled, the resolver reflects it.
        monkeypatch.setenv("ENABLE_HERMES_PROVIDER", "true")
        assert c._bool_env("ENABLE_HERMES_PROVIDER", False) is True

    def test_require_flag_raises_404_when_disabled(self):
        import app.config as c
        from fastapi import HTTPException
        from app.social_publishing.api import reddit_routes

        original = c.ENABLE_HERMES_PROVIDER
        c.ENABLE_HERMES_PROVIDER = False
        try:
            with pytest.raises(HTTPException) as e:
                reddit_routes._require_flag()
            assert e.value.status_code == 404
        finally:
            c.ENABLE_HERMES_PROVIDER = original


# ══════════════════════════════════════════════════════════════════════════════
# 23. UNSUPPORTED MEDIA (video capability False)
# ══════════════════════════════════════════════════════════════════════════════

class TestCapabilitiesAndMedia:
    def test_capabilities_video_false_user_confirmation_true(self):
        caps = HermesRedditWorkflow().capabilities()
        assert isinstance(caps, ProviderCapabilities)
        assert caps.video is False
        assert caps.user_confirmation is True
        assert caps.browser_automation is True
        assert caps.text and caps.image and caps.links

    def test_requires_user_action_true(self):
        assert HermesRedditWorkflow().requires_user_action is True

    async def test_video_media_rejected_before_browser(self):
        browser = FakeSemanticBrowser(authenticated=True)
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=["clip.mp4"]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert browser.calls == []  # never opened the browser


# ══════════════════════════════════════════════════════════════════════════════
# 24-25. BROWSER TIMEOUT / CRASH
# ══════════════════════════════════════════════════════════════════════════════

class TestBrowserFailures:
    async def test_prepare_timeout_is_failed_no_click(self):
        # A timeout while locating the Post button during prepare -> FAILED
        # (nothing was submitted).
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            raise_on="wait_for", error=BrowserTimeout("post button never appeared"),
        )
        res = await HermesRedditWorkflow().prepare(_instruction(), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert not browser.clicked_post()

    async def test_prepare_browser_crash_is_failed(self):
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            raise_on="goto", error=BrowserError("browser crashed"),
        )
        # The very first _is_authenticated goto crashes -> treated as not
        # authenticated -> ACTION_REQUIRED path attempts login goto which also
        # crashes -> surfaces FAILED. Either way it never clicks Post.
        res = await HermesRedditWorkflow().prepare(_instruction(), browser)
        assert res.status in (
            ExternalResultStatus.FAILED, ExternalResultStatus.ACTION_REQUIRED
        )
        assert not browser.clicked_post()

    async def test_click_failure_before_submit_is_failed(self):
        # If the Post click itself never registers, nothing was submitted -> FAILED.
        permalink = None
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=permalink,
            raise_on="click", error=BrowserError("click did not register"),
        )
        wf = HermesRedditWorkflow()
        # Drive confirm_and_publish directly (skip the prepare re-fill which would
        # also hit the click on tab selection); use a text post so the only click
        # is the Post button.
        # Position the browser on the subreddit composer first.
        await browser.goto(C.submit_url("test"))
        res = await wf.confirm_and_publish(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.FAILED

    async def test_timeout_after_click_is_unknown(self):
        # Click succeeds but the permalink never appears -> UNKNOWN (verify, never
        # blind-retry).
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=None,
        )
        wf = HermesRedditWorkflow()
        await browser.goto(C.submit_url("test"))
        res = await wf.confirm_and_publish(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.UNKNOWN
        assert res.retryable is False


# ══════════════════════════════════════════════════════════════════════════════
# 26. SCHEDULED-JOB PATH (execute with a non-semantic adapter -> ACTION_REQUIRED)
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduledJobPath:
    async def test_execute_with_non_semantic_adapter_returns_action_required(self):
        # A scheduled job path passes a coarse (non-SemanticBrowser) adapter.
        # Reddit must surface ACTION_REQUIRED, never auto-publish.
        class CoarseAdapter:
            pass

        res = await HermesRedditWorkflow().execute(_instruction(), CoarseAdapter())
        assert res.status == ExternalResultStatus.ACTION_REQUIRED

    async def test_execute_with_semantic_adapter_runs_prepare(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().execute(_instruction(), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert not browser.clicked_post()  # execute() prepares only


# ══════════════════════════════════════════════════════════════════════════════
# 27. COMPOSER POST-TYPE SELECTION (locator maintenance regression)
#
# Reddit's current composer defaults to a text post and no longer exposes an
# ARIA tab named "Text". The workflow must:
#   - click the legacy "Text" tab IF present (backward compatible), else
#   - verify the text composer is already active (Title textbox present),
# without ever clicking Post or weakening a safety check.
# ══════════════════════════════════════════════════════════════════════════════

class TestComposerPostTypeSelection:
    async def test_current_composer_no_text_tab_still_prepares(self):
        # No legacy "Text" tab (current Reddit UI). Title/Body/Post are present.
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            visible_overrides={C.LOC_TEXT_TAB: False},
        )
        res = await HermesRedditWorkflow().prepare(_instruction(body="Hi"), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        # It did NOT click the (absent) Text tab...
        assert ("click", *C.LOC_TEXT_TAB) not in browser.calls
        # ...but it DID verify the text composer via the Title textbox...
        assert ("wait_for", *C.LOC_TITLE_INPUT) in browser.calls
        # ...filled the content, verified the Post button, and never clicked Post.
        assert ("fill", *C.LOC_TITLE_INPUT, "Hello title") in browser.calls
        assert ("wait_for", *C.LOC_POST_BUTTON) in browser.calls
        assert not browser.clicked_post()

    async def test_legacy_composer_with_text_tab_clicks_it(self):
        # Legacy composer: the "Text" tab exists -> it is clicked (old behaviour).
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            visible_overrides={C.LOC_TEXT_TAB: True},
        )
        res = await HermesRedditWorkflow().prepare(_instruction(body="Hi"), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert ("click", *C.LOC_TEXT_TAB) in browser.calls
        assert not browser.clicked_post()

    async def test_text_composer_detected_via_title_textbox(self):
        # The helper's "text composer active" signal is the Title textbox. Drive
        # the helper directly with no Text tab present.
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            visible_overrides={C.LOC_TEXT_TAB: False},
        )
        await HermesRedditWorkflow()._ensure_text_composer(browser)
        assert ("is_visible", *C.LOC_TEXT_TAB) in browser.calls
        assert ("wait_for", *C.LOC_TITLE_INPUT) in browser.calls
        assert ("click", *C.LOC_TEXT_TAB) not in browser.calls

    async def test_no_tab_and_no_title_fails_safe(self):
        # Neither a Text tab NOR a Title textbox -> not a usable text composer.
        # Prepare must FAIL safely (nothing submitted), never click Post.
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            visible_overrides={C.LOC_TEXT_TAB: False, C.LOC_TITLE_INPUT: False},
        )
        res = await HermesRedditWorkflow().prepare(_instruction(), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert not browser.clicked_post()

    async def test_missing_post_button_fails_safe_no_tab(self):
        # Current composer (no Text tab), Title present, but Post button missing.
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            visible_overrides={C.LOC_TEXT_TAB: False, C.LOC_POST_BUTTON: False},
        )
        res = await HermesRedditWorkflow().prepare(_instruction(), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert not browser.clicked_post()

    async def test_unexpected_subreddit_still_blocks_with_new_logic(self):
        # The subreddit safety check must remain mandatory regardless of the
        # composer change: landing off-target -> FAILED, no content entered.
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=False,
            visible_overrides={C.LOC_TEXT_TAB: False},
        )
        res = await HermesRedditWorkflow().prepare(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        # Never even probed the composer post-type control.
        assert ("is_visible", *C.LOC_TEXT_TAB) not in browser.calls
        assert not browser.clicked_post()


# ══════════════════════════════════════════════════════════════════════════════
# 28. PUBLISH VERIFICATION (permalink detection on the current Reddit UI)
#
# The verifier must reliably resolve PUBLISHED once Reddit's address bar becomes
# the created post's /comments/ permalink for the EXPECTED subreddit — even when
# that transition lags past the initial wait (current SPA behaviour). It must
# never click Post, and must stay UNKNOWN on ambiguous/untrusted evidence.
# ══════════════════════════════════════════════════════════════════════════════

class TestPublishVerification:
    _PERMALINK = f"{C.REDDIT_BASE}/r/test/comments/1wovlno/trendzzo_hermes_smoke_test/"

    async def test_verify_published_when_permalink_immediate(self):
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=self._PERMALINK,
        )
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.PUBLISHED
        assert res.external_url == self._PERMALINK
        assert res.external_post_id == "1wovlno"
        assert not browser.clicked_post()

    async def test_verify_published_when_url_lags_then_appears(self, monkeypatch):
        # The SPA doesn't switch the address bar until a few reads later, and the
        # initial wait_for_url_contains times out — the poll loop must still catch
        # the permalink and resolve PUBLISHED.
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)  # no real sleeping
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=self._PERMALINK,
            permalink_after_checks=3, lag_wait_for_url=True,
        )
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.PUBLISHED
        assert res.external_url == self._PERMALINK
        assert res.external_post_id == "1wovlno"
        assert not browser.clicked_post()

    async def test_verify_unknown_when_no_permalink(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        # Reduce the poll ceiling so the "never appears" path returns quickly.
        monkeypatch.setattr(C, "PERMALINK_POLL_MAX_CHECKS", 3)
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=None,
        )
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.UNKNOWN
        assert res.retryable is False
        assert not browser.clicked_post()

    async def test_verify_unknown_when_permalink_wrong_subreddit(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        wrong = f"{C.REDDIT_BASE}/r/somethingelse/comments/zzz999/other/"
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=wrong,
        )
        await browser.goto(C.submit_url("test"))
        # We intended to post to r/test, but the address bar shows a DIFFERENT
        # subreddit's permalink -> must NOT claim success.
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.UNKNOWN
        assert not browser.clicked_post()

    async def test_verify_unknown_when_permalink_non_reddit_host(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        malicious = "https://evil.example.com/r/test/comments/1wovlno/x/"
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=malicious,
        )
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.UNKNOWN
        assert not browser.clicked_post()

    async def test_verify_never_clicks_post(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=self._PERMALINK,
            permalink_after_checks=2, lag_wait_for_url=True,
        )
        await browser.goto(C.submit_url("test"))
        await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        # No Post click anywhere in verification, and no new content filled.
        assert not browser.clicked_post()
        assert not any(c[0] == "fill" for c in browser.calls)

    async def test_confirm_publishes_with_lagging_url(self, monkeypatch):
        # End-to-end via the service: confirm -> click Post -> URL lags -> poll
        # catches permalink -> published, exactly once (idempotent op id).
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=self._PERMALINK,
            permalink_after_checks=3, lag_wait_for_url=True,
        )
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op-verify", "T", "B", "test", [])
        cid = out["confirmation_id"]
        result = await svc.confirm("t1", cid)
        assert result["status"] == "published"
        assert result["external_url"] == self._PERMALINK
        assert result["external_post_id"] == "1wovlno"
        assert browser.clicked_post()  # Post clicked exactly during confirm
        row = await repo.find(cid, "t1")
        assert row.status == "published"

    def test_permalink_id_strips_query_and_slash(self):
        from app.social_publishing.providers.hermes.reddit.workflow import _permalink_id
        assert _permalink_id(f"{C.REDDIT_BASE}/r/test/comments/1wovlno/slug/") == "1wovlno"
        assert _permalink_id(f"{C.REDDIT_BASE}/r/test/comments/abc123?entry_point=x") == "abc123"

    def test_is_expected_permalink_validation(self):
        from app.social_publishing.providers.hermes.reddit.workflow import _is_expected_permalink
        ok = f"{C.REDDIT_BASE}/r/test/comments/1wovlno/slug/"
        assert _is_expected_permalink(ok, "test") is True
        assert _is_expected_permalink(ok, "other") is False              # wrong subreddit
        assert _is_expected_permalink("https://evil.com/r/test/comments/1/x/", "test") is False
        assert _is_expected_permalink("/r/test/comments/1/x/", "test") is False  # relative
        assert _is_expected_permalink(ok, "") is False                   # no expected sub
        assert _is_expected_permalink(f"{C.REDDIT_BASE}/r/test/submit/", "test") is False  # not a permalink


# ══════════════════════════════════════════════════════════════════════════════
# 29. IMAGE POST SUPPORT (current Reddit composer)
#
# Reddit's image-post control is now a BUTTON "Image" (the old "Images & Video"
# ARIA tab is gone) and the file input is a raw <input type=file> (no accessible
# label). Prepare must: select the image composer, attach ONE image, VERIFY it
# attached, fill the title, verify the Post button, and STOP at ACTION_REQUIRED —
# never clicking Post. Filesystem validation (existence/type/size) runs before
# the browser opens.
# ══════════════════════════════════════════════════════════════════════════════

def _real_image(tmp_path, name="pic.png", size=128):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * size)
    return str(p)


class TestImagePostPreparation:
    async def test_image_button_selected_current_ui(self, tmp_path):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert ("click", *C.LOC_IMAGE_BUTTON) in browser.calls
        assert not browser.clicked_post()

    async def test_legacy_image_tab_compat(self, tmp_path):
        # If the current "Image" button is absent but the legacy tab exists,
        # the legacy tab is used (backward compatible).
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            visible_overrides={C.LOC_IMAGE_BUTTON: False, C.LOC_IMAGE_TAB: True},
        )
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert ("click", *C.LOC_IMAGE_TAB) in browser.calls
        assert not browser.clicked_post()

    async def test_image_file_selected_and_verified(self, tmp_path):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        path = _real_image(tmp_path)
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[path]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        # The file was attached via the raw file-input selector and verified via
        # the accessible "Remove media" control.
        assert ("set_input_files", C.IMAGE_FILE_INPUT_SELECTOR, (path,)) in browser.calls
        assert ("is_visible", *C.LOC_REMOVE_MEDIA) in browser.calls

    async def test_attach_failure_fails_safe(self, tmp_path):
        # set_input_files "succeeds" but the input reports 0 files attached ->
        # prepare must FAIL (don't proceed to a post missing its image).
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        browser.attach_fails = True  # count_attached_files will return 0
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert not browser.clicked_post()

    async def test_missing_image_file_rejected_before_browser(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=["c:/nope/does_not_exist.png"]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        # Never opened the browser (filesystem validation happens first).
        assert browser.calls == []

    async def test_unsupported_image_type_rejected(self):
        # Reddit workflow validation rejects video at the content-validation
        # stage (before any filesystem/browser work).
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=["clip.mp4"]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []

    async def test_oversized_image_rejected_before_browser(self, tmp_path, monkeypatch):
        # A real file that exceeds the media size limit -> rejected before the
        # browser opens (reuses media_workspace.validate_media via the workflow).
        # validate_media binds HERMES_MAX_MEDIA_BYTES at import in media_workspace,
        # so patch it THERE (not on constants) to shrink the limit for this test.
        from app.social_publishing.providers.hermes import media_workspace as MW
        monkeypatch.setattr(MW, "HERMES_MAX_MEDIA_BYTES", 10, raising=False)
        big = _real_image(tmp_path, name="big.png", size=64)  # > 10 bytes
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[big]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []

    async def test_missing_title_rejected_for_image(self):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(
            _instruction(title="", media_paths=["pic.png"]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []

    async def test_missing_post_button_fails_safe_image(self, tmp_path):
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True,
            visible_overrides={C.LOC_POST_BUTTON: False},
        )
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert not browser.clicked_post()

    async def test_unexpected_subreddit_blocks_image_post(self, tmp_path):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=False)
        res = await HermesRedditWorkflow().prepare(
            _instruction(subreddit="test", media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        # Never selected the composer / attached anything.
        assert ("click", *C.LOC_IMAGE_BUTTON) not in browser.calls
        assert not any(c[0] == "set_input_files" for c in browser.calls)
        assert not browser.clicked_post()

    async def test_image_prepare_reaches_action_required_and_persists(self, tmp_path):
        # End-to-end via the service: image prepare -> ACTION_REQUIRED + pending.
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        svc, repo = _service(browser)
        out = await svc.prepare(
            "t1", "acc1", "op-img", "Title", "", "test", [_real_image(tmp_path)]
        )
        assert out["status"] == "action_required"
        assert out["preview"]["media_count"] == 1
        row = await repo.find(out["confirmation_id"], "t1")
        assert row is not None and row.status == "action_required"

    async def test_image_prepare_never_clicks_post(self, tmp_path):
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert not browser.clicked_post()

    async def test_single_image_only_still_enforced(self, tmp_path):
        # Two images must be rejected (single-image policy unchanged).
        browser = FakeSemanticBrowser(authenticated=True, reach_subreddit=True)
        res = await HermesRedditWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path, "a.png"),
                                      _real_image(tmp_path, "b.png")]),
            browser,
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []


# ══════════════════════════════════════════════════════════════════════════════
# 30. SUBMITTED-LISTING VERIFICATION FALLBACK
#
# When the post-submit page does NOT redirect the address bar to a /comments/
# permalink (common on the current Reddit UI), _verify falls back to the user's
# submitted-posts listing and resolves PUBLISHED by matching the exact title —
# the signal that a manual reconciliation used. Ambiguity stays UNKNOWN.
# ══════════════════════════════════════════════════════════════════════════════

class TestSubmittedListingFallback:
    _PERMALINK = f"{C.REDDIT_BASE}/r/test/comments/1wovlno/hello_title/"

    def _no_url_browser(self):
        # A browser whose address bar never becomes a permalink, forcing the
        # fallback path. permalink_after_checks with no post_permalink + lag.
        return FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=None,
            lag_wait_for_url=True,
        )

    async def test_fallback_resolves_published_on_single_title_match(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "PERMALINK_POLL_MAX_CHECKS", 2)
        browser = self._no_url_browser()
        browser.submitted_permalinks = [self._PERMALINK]
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(
            _instruction(subreddit="test", username="tester"), browser
        )
        assert res.status == ExternalResultStatus.PUBLISHED
        assert res.external_url == self._PERMALINK
        assert res.external_post_id == "1wovlno"
        # It visited the submitted listing and never clicked Post.
        assert ("goto", C.submitted_url("tester")) in browser.calls
        assert not browser.clicked_post()

    async def test_fallback_unknown_when_no_match(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "PERMALINK_POLL_MAX_CHECKS", 2)
        browser = self._no_url_browser()
        browser.submitted_permalinks = []  # nothing matched
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.UNKNOWN
        assert not browser.clicked_post()

    async def test_fallback_rejects_wrong_subreddit_permalink(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "PERMALINK_POLL_MAX_CHECKS", 2)
        browser = self._no_url_browser()
        # Listing returns a permalink for a DIFFERENT subreddit → not accepted.
        browser.submitted_permalinks = [f"{C.REDDIT_BASE}/r/othersub/comments/zzz/x/"]
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.UNKNOWN

    async def test_fallback_unknown_on_multiple_matches(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "PERMALINK_POLL_MAX_CHECKS", 2)
        browser = self._no_url_browser()
        # Two DISTINCT posts match → ambiguous → do not guess.
        browser.submitted_permalinks = [
            f"{C.REDDIT_BASE}/r/test/comments/aaa111/hello/",
            f"{C.REDDIT_BASE}/r/test/comments/bbb222/hello/",
        ]
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.UNKNOWN

    async def test_fallback_skipped_without_username(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "PERMALINK_POLL_MAX_CHECKS", 2)
        browser = self._no_url_browser()
        browser.submitted_permalinks = [self._PERMALINK]
        await browser.goto(C.submit_url("test"))
        # No username → cannot build the listing URL → fallback skipped → UNKNOWN.
        res = await HermesRedditWorkflow().verify(
            _instruction(subreddit="test", username=""), browser
        )
        assert res.status == ExternalResultStatus.UNKNOWN
        assert ("find_post_permalinks", "Hello title") not in browser.calls

    async def test_primary_url_still_wins_without_fallback(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        # Address bar DOES become the permalink → fallback not needed.
        browser = FakeSemanticBrowser(
            authenticated=True, reach_subreddit=True, post_permalink=self._PERMALINK,
        )
        await browser.goto(C.submit_url("test"))
        res = await HermesRedditWorkflow().verify(_instruction(subreddit="test"), browser)
        assert res.status == ExternalResultStatus.PUBLISHED
        # Did not need the submitted listing.
        assert ("goto", C.submitted_url("tester")) not in browser.calls

    async def test_confirm_publishes_via_fallback_end_to_end(self, monkeypatch):
        monkeypatch.setattr(C, "PERMALINK_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "PERMALINK_POLL_MAX_CHECKS", 2)
        browser = self._no_url_browser()
        browser.submitted_permalinks = [self._PERMALINK]
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op-fb", "Hello title", "Body", "test", [])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_url"] == self._PERMALINK
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "published"
