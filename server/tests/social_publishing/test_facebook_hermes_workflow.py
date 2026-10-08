"""Facebook user-assisted Hermes workflow tests (profile + page targets).

A scripted fake SemanticBrowser + in-memory pending repo drive the two-phase
(prepare → confirm) flow deterministically — no real browser, no Playwright, no
network, no database.

Facebook specifics vs Instagram:
  - ONE workflow serves BOTH targets (profile vs page), chosen by target_type.
  - text-only is allowed; image is OPTIONAL (at most one).
  - success signal is a post permalink and/or a "published" confirmation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
)
from app.social_publishing.providers.hermes.facebook import constants as C
from app.social_publishing.providers.hermes.facebook.validation import validate_post
from app.social_publishing.providers.hermes.facebook.workflow import (
    HermesFacebookWorkflow,
    _is_facebook_permalink,
    _permalink_id,
)
from app.social_publishing.providers.hermes.facebook.service import FacebookUserAssistedService
from app.social_publishing.providers.hermes.semantic_browser import (
    BrowserError,
    BrowserTimeout,
    SemanticBrowser,
)


# ══════════════════════════════════════════════════════════════════════════════
# Scripted fake SemanticBrowser tailored to the Facebook flow
# ══════════════════════════════════════════════════════════════════════════════

def _default_visible(target_type=C.TARGET_PROFILE):
    """A healthy authenticated composer for the given target."""
    vis = set()
    # Composer-open entry control for the target + the in-composer controls.
    vis.update(C.LOC_OPEN_COMPOSER_CANDIDATES[target_type][:1])
    vis.update(C.LOC_TEXT_EDITOR_CANDIDATES[:1])
    vis.update(C.LOC_ADD_PHOTO_CANDIDATES[:1])
    vis.update(C.LOC_POST_BUTTON_CANDIDATES[:1])
    return vis


class FakeFBBrowser:
    def __init__(
        self,
        *,
        authenticated=True,
        target_type=C.TARGET_PROFILE,
        attached=1,
        bg_preview=0,
        file_input_present=1,
        permalink=None,
        published_confirmation=False,
        raise_on=None,
        error=None,
        visible=None,
        composer_opens=True,
        post_css_matches=None,
        closes_on_post=True,
    ):
        self.authenticated = authenticated
        self.target_type = target_type
        # Real Facebook composers dismiss the Create-post dialog once Post is
        # clicked — the reliable success signal. Set False to model a hung
        # composer (no success signal) for the UNKNOWN path.
        self._closes_on_post = closes_on_post
        # Which dialog-scoped Post CSS selectors "exist" and are clickable. By
        # default the primary (most specific) selector matches, modelling the
        # real Facebook composer's aria-labelled Post button.
        self._post_css_matches = set(
            post_css_matches if post_css_matches is not None
            else {C.POST_BUTTON_CSS_CANDIDATES[0]}
        )
        self._attached = attached
        self._bg_preview = bg_preview
        self._file_input_present = file_input_present
        self.permalink = permalink
        self.published_confirmation = published_confirmation
        self.raise_on = raise_on
        self.error = error or BrowserError("scripted failure")
        self._visible = set(visible) if visible is not None else _default_visible(target_type)
        self._url = C.HOME_URL
        self.calls = []
        self.started = False
        self.closed = False
        self._posted = False
        self._composer_open_flag = False
        self._composer_opens = composer_opens

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
        self._url = url

    async def current_url(self):
        self._maybe_raise("current_url")
        if self._posted and self.permalink:
            return self.permalink
        return self._url

    async def is_visible(self, role, name, *, timeout_s=None):
        self.calls.append(("is_visible", role, name))
        self._maybe_raise("is_visible")
        if (role, name) in set(C.LOC_LOGIN_SIGNAL_CANDIDATES):
            return not self.authenticated
        if (role, name) in set(C.POST_SUCCESS_TEXT_CANDIDATES):
            return self._posted and self.published_confirmation
        # The composer dialog title / text editor only "appear" while the
        # composer is open (so once it closes on Post, _composer_open is False).
        if (role, name) in set(C.LOC_COMPOSER_DIALOG_TITLE_CANDIDATES):
            return self._composer_open_flag
        if (role, name) in set(C.LOC_TEXT_EDITOR_CANDIDATES):
            return self._composer_open_flag and (role, name) in self._visible
        return (role, name) in self._visible

    async def get_text(self, role, name, *, timeout_s=None):
        self.calls.append(("get_text", role, name))
        return ""

    async def wait_for(self, role, name, *, timeout_s=None):
        self.calls.append(("wait_for", role, name))
        self._maybe_raise("wait_for")

    async def wait_for_url_contains(self, fragment, *, timeout_s=None):
        self.calls.append(("wait_for_url_contains", fragment))
        raise BrowserTimeout("no nav")

    async def fill(self, role, name, value, *, timeout_s=None):
        self.calls.append(("fill", role, name, value))
        self._maybe_raise("fill")

    async def click(self, role, name, *, timeout_s=None):
        self.calls.append(("click", role, name))
        self._maybe_raise("click")
        # Clicking a composer-open entry control opens the composer.
        if (role, name) in set(C.LOC_OPEN_COMPOSER_CANDIDATES[self.target_type]) and self._composer_opens:
            self._composer_open_flag = True
        if (role, name) in set(C.LOC_POST_BUTTON_CANDIDATES):
            self._posted = True
            if self._closes_on_post:
                self._composer_open_flag = False  # dialog dismissed on Post

    async def set_input_files(self, label, file_paths, *, timeout_s=None):
        self.calls.append(("set_input_files", label, tuple(file_paths)))
        self._maybe_raise("set_input_files")

    async def count_attached_files(self, selector=C.IMAGE_FILE_INPUT_SELECTOR, *, timeout_s=None):
        self.calls.append(("count_attached_files", selector))
        # Both the dialog-scoped and generic file-input selectors report the
        # (hidden) uploader presence.
        if selector in (C.IMAGE_FILE_INPUT_SELECTOR, C.IMAGE_FILE_INPUT_SELECTOR_GENERIC):
            return self._file_input_present
        if selector == C.IMAGE_PREVIEW_BG_SELECTOR:
            return self._bg_preview
        return self._attached

    async def find_post_permalinks(self, title, *, timeout_s=None):
        self.calls.append(("find_post_permalinks", title))
        return []

    async def click_svg_anchor(self, svg_aria_label, *, timeout_s=None):
        self.calls.append(("click_svg_anchor", svg_aria_label))
        return False

    async def is_css_visible(self, selector, *, timeout_s=None):
        self.calls.append(("is_css_visible", selector))
        self._maybe_raise("is_css_visible")
        if selector == C.COMPOSER_DIALOG_SELECTOR:
            return self._composer_open_flag
        # The composer's dialog-scoped Post button is present while the composer
        # is open and disappears once the post is accepted — the signal the
        # verification poller uses to detect a successful publish.
        if selector in self._post_css_matches:
            return self._composer_open_flag
        return False

    async def click_css(self, selector, *, timeout_s=None):
        self.calls.append(("click_css", selector))
        self._maybe_raise("click_css")
        # The scoped Post-button CSS selectors submit the post when matchable.
        if selector in self._post_css_matches:
            self._posted = True
            if self._closes_on_post:
                self._composer_open_flag = False  # dialog dismissed on Post
            return True
        return False

    async def screenshot(self, path):
        self.calls.append(("screenshot", path))

    # helpers
    def clicked_post(self):
        via_role = any(
            c[0] == "click" and (c[1], c[2]) in set(C.LOC_POST_BUTTON_CANDIDATES)
            for c in self.calls if len(c) >= 3
        )
        via_css = any(
            c[0] == "click_css" and c[1] in self._post_css_matches
            for c in self.calls if len(c) >= 2
        )
        return via_role or via_css


# ══════════════════════════════════════════════════════════════════════════════
# In-memory pending repo
# ══════════════════════════════════════════════════════════════════════════════

class FakePendingRepo:
    def __init__(self):
        self._rows = {}
        self._seq = 0

    async def create(self, tenant_id, account_id, operation_id, platform,
                     provider_name, *, title="", body="", subreddit="",
                     media_paths=None, session_profile_key="", ttl_seconds=900):
        now = datetime.now(timezone.utc)
        for cid, row in self._rows.items():
            if row["operation_id"] == operation_id and row["tenant_id"] == tenant_id:
                row.update(title=title, body=body, subreddit=subreddit,
                           media_paths=list(media_paths or []),
                           session_profile_key=session_profile_key, status="action_required",
                           expires_at=now + timedelta(seconds=ttl_seconds))
                return _Row(cid, row)
        self._seq += 1
        cid = f"c{self._seq}"
        self._rows[cid] = {
            "tenant_id": tenant_id, "account_id": account_id, "operation_id": operation_id,
            "platform": platform, "provider_name": provider_name, "status": "action_required",
            "title": title, "body": body, "subreddit": subreddit,
            "media_paths": list(media_paths or []), "session_profile_key": session_profile_key,
            "external_url": None, "created_at": now, "updated_at": now,
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
        return [_Row(cid, row) for cid, row in self._rows.items()
                if row["tenant_id"] == tenant_id
                and row["status"] in ("action_required", "waiting_for_user")]

    async def set_status(self, confirmation_id, tenant_id, status, *, external_url=None):
        row = self._rows.get(confirmation_id)
        if not row or row["tenant_id"] != tenant_id:
            return False
        row["status"] = status
        if external_url is not None:
            row["external_url"] = external_url
        return True


class _Row:
    def __init__(self, cid, row):
        self.id = cid
        for k, v in row.items():
            setattr(self, k, v)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers / fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch):
    monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0, raising=False)
    monkeypatch.setattr(C, "POST_POLL_MAX_CHECKS", 3, raising=False)


@pytest.fixture(autouse=True)
def _clear_live_session_cache():
    """Clear the process-wide live-session cache between tests."""
    from app.social_publishing.providers.hermes.live_session_cache import live_session_cache
    live_session_cache._by_confirmation.clear()
    yield
    live_session_cache._by_confirmation.clear()


def _instruction(text="Hello Facebook", media_paths=None, op="op1", tenant="t1",
                 account="acc1", target_type=C.TARGET_PROFILE, target_identifier=""):
    return PublishInstruction(
        operation_id=op, tenant_id=tenant, account_id=account,
        platform=C.PLATFORM, provider_name="hermes", text=text,
        meta={
            "facebook_text": text,
            "facebook_media_paths": media_paths or [],
            "facebook_target_type": target_type,
            "facebook_target_identifier": target_identifier,
        },
    )


def _real_image(tmp_path, name="pic.jpg", size=128):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff" + b"0" * size)
    return str(p)


def _service(browser, pending=None, target=None):
    repo = pending or FakePendingRepo()
    svc = FacebookUserAssistedService(
        pending_repo=repo,
        workflow=HermesFacebookWorkflow(),
        adapter_factory=lambda profile_key: browser,
        target_resolver=lambda account_id, tenant_id: _async_return(
            target or {"target_type": C.TARGET_PROFILE, "target_identifier": "", "target_name": "Me"}
        ),
    )
    return svc, repo


def _async_return(value):
    async def _c():
        return value
    return _c()


# ══════════════════════════════════════════════════════════════════════════════
# CAPABILITIES / ROUTING
# ══════════════════════════════════════════════════════════════════════════════

class TestRegistrationAndRouting:
    def test_platform_and_capabilities(self):
        wf = HermesFacebookWorkflow()
        assert wf.platform == C.PLATFORM == "facebook"
        caps = wf.capabilities()
        assert isinstance(caps, ProviderCapabilities)
        assert caps.text is True
        assert caps.image is True
        assert caps.video is False
        assert caps.browser_automation is True
        assert caps.user_confirmation is True
        assert wf.requires_user_action is True
        assert wf.automation_allowed is True

    def test_registers_on_user_assisted_hermes_provider(self):
        from app.social_publishing.providers.hermes.provider import HermesPublisherProvider
        from app.social_publishing.providers.hermes.automation_policy import PlatformAutomationPolicy
        from app.social_publishing.providers.hermes.adapter import FakeHermesExecutionAdapter

        policy = PlatformAutomationPolicy(); policy.allow(C.PLATFORM)
        prov = HermesPublisherProvider(
            adapter_factory=lambda: FakeHermesExecutionAdapter(),
            automation_policy=policy, user_assisted=True,
        )
        prov.register_workflow(HermesFacebookWorkflow())
        assert C.PLATFORM in getattr(prov, "_workflows", {})
        assert prov.capabilities(C.PLATFORM).text is True

    def test_router_facebook_native_vs_user_assisted(self):
        from app.social_publishing.providers.router import ProviderRouter
        from app.social_publishing.providers.hermes.provider import HermesPublisherProvider
        from app.social_publishing.providers.hermes.automation_policy import PlatformAutomationPolicy
        from app.social_publishing.providers.hermes.adapter import FakeHermesExecutionAdapter
        from app.social_publishing.domain.enums import ProviderType, ProviderName, SocialPlatform
        from app.social_publishing.domain.models import SocialAccount

        policy = PlatformAutomationPolicy(); policy.allow(C.PLATFORM)
        hermes = HermesPublisherProvider(
            adapter_factory=lambda: FakeHermesExecutionAdapter(),
            automation_policy=policy, user_assisted=True,
        )
        hermes.register_workflow(HermesFacebookWorkflow())

        class _FakeNative:
            provider_name = ProviderName.META.value
            provider_type = ProviderType.NATIVE_API

        router = ProviderRouter()
        router.register(_FakeNative())
        router.register(hermes)

        native_acct = SocialAccount(
            id="a1", tenant_id="t1", platform=SocialPlatform.FACEBOOK,
            account_name="n", platform_account_id="PA1",
            provider_type=ProviderType.NATIVE_API, provider_name=ProviderName.META.value,
        )
        hermes_acct = SocialAccount(
            id="a2", tenant_id="t1", platform=SocialPlatform.FACEBOOK,
            account_name="n", platform_account_id="PA2",
            provider_type=ProviderType.USER_ASSISTED_AGENT, provider_name=ProviderName.HERMES.value,
        )
        assert router.resolve(native_acct) is not hermes
        assert router.resolve(hermes_acct) is hermes


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation:
    def test_text_only_valid(self):
        parsed, errors = validate_post("hello", [], C.TARGET_PROFILE)
        assert errors == [] and parsed.text == "hello" and parsed.media_paths == []

    def test_image_plus_text_valid(self):
        parsed, errors = validate_post("hi", ["a.jpg"], C.TARGET_PAGE)
        assert errors == [] and parsed.target_type == "page"

    def test_image_only_valid(self):
        parsed, errors = validate_post("", ["a.png"], C.TARGET_PROFILE)
        assert errors == [] and parsed.text == ""

    def test_empty_post_rejected(self):
        parsed, errors = validate_post("", [], C.TARGET_PROFILE)
        assert parsed is None and any("text or an image" in e.lower() for e in errors)

    def test_multiple_images_rejected(self):
        parsed, errors = validate_post("x", ["a.jpg", "b.jpg"], C.TARGET_PROFILE)
        assert parsed is None and any("at most" in e.lower() for e in errors)

    def test_unsupported_type_rejected(self):
        parsed, errors = validate_post("x", ["clip.mp4"], C.TARGET_PROFILE)
        assert parsed is None and any("mp4" in e.lower() or "unsupported" in e.lower() for e in errors)

    def test_unknown_target_rejected(self):
        parsed, errors = validate_post("hi", [], "group")
        assert parsed is None and any("target_type" in e.lower() for e in errors)


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthentication:
    async def test_not_authenticated_requires_action(self):
        browser = FakeFBBrowser(authenticated=False)
        res = await HermesFacebookWorkflow().prepare(_instruction(), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert res.error_code == ExternalErrorCode.ACTION_REQUIRED
        assert ("goto", C.LOGIN_URL) in browser.calls
        assert not browser.clicked_post()


# ══════════════════════════════════════════════════════════════════════════════
# PREPARE — profile + page, text-only + image
# ══════════════════════════════════════════════════════════════════════════════

class TestPreparation:
    async def test_profile_text_only_reaches_action_required(self):
        browser = FakeFBBrowser(target_type=C.TARGET_PROFILE)
        res = await HermesFacebookWorkflow().prepare(
            _instruction(text="hello profile", target_type=C.TARGET_PROFILE), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert ("goto", C.PROFILE_URL) in browser.calls          # profile navigation
        assert any(c[0] == "fill" for c in browser.calls)         # text typed
        assert not any(c[0] == "set_input_files" for c in browser.calls)  # no image
        assert not browser.clicked_post()

    async def test_page_text_only_navigates_to_page(self):
        browser = FakeFBBrowser(target_type=C.TARGET_PAGE)
        res = await HermesFacebookWorkflow().prepare(
            _instruction(text="hello page", target_type=C.TARGET_PAGE,
                         target_identifier="my-page"), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert ("goto", C.page_url("my-page")) in browser.calls   # page navigation
        assert not browser.clicked_post()

    async def test_profile_image_plus_text(self, tmp_path):
        browser = FakeFBBrowser(target_type=C.TARGET_PROFILE, attached=1)
        res = await HermesFacebookWorkflow().prepare(
            _instruction(text="with image", media_paths=[_real_image(tmp_path)],
                         target_type=C.TARGET_PROFILE), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert any(c[0] == "set_input_files" for c in browser.calls)
        assert not browser.clicked_post()

    async def test_image_attach_never_clicks_photo_button(self, tmp_path):
        # The image is set DIRECTLY on the hidden file input via set_input_files.
        # The "Photo/video" control must NEVER be clicked (clicking it opens the
        # OS file picker and blocks). Guards against the file-dialog regression.
        browser = FakeFBBrowser(target_type=C.TARGET_PROFILE, attached=1)
        res = await HermesFacebookWorkflow().prepare(
            _instruction(text="with image", media_paths=[_real_image(tmp_path)],
                         target_type=C.TARGET_PROFILE), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert any(c[0] == "set_input_files" for c in browser.calls)
        # No click on any Photo/video candidate.
        assert not any(
            c[0] == "click" and (c[1], c[2]) in set(C.LOC_ADD_PHOTO_CANDIDATES)
            for c in browser.calls if len(c) >= 3
        )

    async def test_image_attach_failure_fails_safe(self, tmp_path):
        browser = FakeFBBrowser(attached=0, bg_preview=0)
        res = await HermesFacebookWorkflow().prepare(
            _instruction(text="x", media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert not browser.clicked_post()

    async def test_composer_never_opens_fails(self):
        browser = FakeFBBrowser(composer_opens=False)
        # Keep only the composer-entry control visible so auth passes, but the
        # composer never opens (text editor / dialog never appear).
        browser._visible = set(C.LOC_OPEN_COMPOSER_CANDIDATES[C.TARGET_PROFILE][:1])
        res = await HermesFacebookWorkflow().prepare(_instruction(text="hello"), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert "composer" in (res.error_message or "").lower()
        assert not browser.clicked_post()

    async def test_empty_post_fails_before_browser(self):
        browser = FakeFBBrowser()
        res = await HermesFacebookWorkflow().prepare(_instruction(text="", media_paths=[]), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []

    async def test_missing_image_on_disk_fails(self):
        browser = FakeFBBrowser()
        res = await HermesFacebookWorkflow().prepare(
            _instruction(text="x", media_paths=["/no/such/file.jpg"]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []


# ══════════════════════════════════════════════════════════════════════════════
# CONFIRM / PUBLISH / VERIFY
# ══════════════════════════════════════════════════════════════════════════════

class TestConfirmPublish:
    async def test_confirm_published_via_permalink_profile(self, monkeypatch):
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        permalink = f"{C.FB_BASE}/me/posts/123456"
        browser = FakeFBBrowser(target_type=C.TARGET_PROFILE, permalink=permalink)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "hello", [])
        assert out["status"] == "action_required"
        assert out["preview"]["target_type"] == "profile"
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_url"] == permalink
        assert res["external_post_id"] == "123456"
        assert browser.clicked_post()
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "published"

    async def test_confirm_published_page_story_fbid(self, monkeypatch):
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        permalink = f"{C.FB_BASE}/permalink.php?story_fbid=99887766&id=5"
        browser = FakeFBBrowser(target_type=C.TARGET_PAGE, permalink=permalink)
        svc, repo = _service(
            browser,
            target={"target_type": C.TARGET_PAGE, "target_identifier": "my-page", "target_name": "My Page"},
        )
        out = await svc.prepare("t1", "acc1", "op1", "hello page", [])
        assert out["preview"]["target_type"] == "page"
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_post_id"] == "99887766"
        assert browser.clicked_post()

    async def test_confirm_unknown_when_no_signal(self, monkeypatch):
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "POST_POLL_MAX_CHECKS", 2)
        # Composer does NOT close after Post and no permalink/toast → no success
        # signal at all → UNKNOWN (never blind-retried).
        browser = FakeFBBrowser(permalink=None, published_confirmation=False,
                                closes_on_post=False)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "hello", [])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "unknown"
        assert browser.clicked_post()
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "unknown"

    async def test_confirm_published_via_composer_closed_no_permalink(self, monkeypatch):
        # The reliable Facebook signal: after Post, the composer dialog closes
        # and there is NO permalink in the URL (typical for profile/page posts).
        # This MUST resolve to PUBLISHED (external_url may be None), NOT hang on
        # UNKNOWN — the bug where the UI stayed stuck on "Publishing…".
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        browser = FakeFBBrowser(permalink=None, published_confirmation=False,
                                closes_on_post=True)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "hello world", [])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_url"] is None   # no permalink on a profile post — fine
        assert browser.clicked_post()
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "published"

    async def test_verify_never_clicks_post(self, monkeypatch):
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "POST_POLL_MAX_CHECKS", 2)
        browser = FakeFBBrowser(permalink=None)
        await HermesFacebookWorkflow().verify(_instruction(), browser)
        assert not browser.clicked_post()


# ══════════════════════════════════════════════════════════════════════════════
# POST-BUTTON LOCATOR (_click_post_button) — dialog-scoped CSS first, role fallback
# ══════════════════════════════════════════════════════════════════════════════

class TestPostButtonLocator:
    """
    The final Post click must prefer a DIALOG-SCOPED CSS selector (Facebook's
    Post is an aria-labelled role=button DIV), then fall back to the accessible
    (role, name) candidates, and fail cleanly when neither is clickable.
    """

    async def test_scoped_css_post_button_clicked_first(self):
        # The primary dialog-scoped CSS selector matches → it is used, and no
        # accessible (role,name) click fallback is attempted.
        browser = FakeFBBrowser()
        ok = await HermesFacebookWorkflow()._click_post_button(browser)
        assert ok is True
        assert ("click_css", C.POST_BUTTON_CSS_CANDIDATES[0]) in browser.calls
        # No role/name click fallback was needed.
        assert not any(
            c[0] == "click" and (c[1], c[2]) in set(C.LOC_POST_BUTTON_CANDIDATES)
            for c in browser.calls if len(c) >= 3
        )

    async def test_falls_back_to_role_name_when_css_absent(self):
        # No scoped CSS Post button exists → fall back to the (role,name)
        # candidate click, which still submits.
        browser = FakeFBBrowser(post_css_matches=set())
        ok = await HermesFacebookWorkflow()._click_post_button(browser)
        assert ok is True
        assert any(c[0] == "click_css" for c in browser.calls)   # tried CSS first
        assert any(
            c[0] == "click" and (c[1], c[2]) in set(C.LOC_POST_BUTTON_CANDIDATES)
            for c in browser.calls if len(c) >= 3
        )

    async def test_fails_when_no_post_button_anywhere(self):
        # Neither a scoped CSS Post button nor a visible (role,name) Post exists.
        browser = FakeFBBrowser(post_css_matches=set())
        browser._visible = browser._visible - set(C.LOC_POST_BUTTON_CANDIDATES)
        ok = await HermesFacebookWorkflow()._click_post_button(browser)
        assert ok is False
        assert not browser.clicked_post()

    async def test_confirm_uses_scoped_css_and_publishes(self, monkeypatch):
        # End-to-end confirm: scoped CSS Post click submits, permalink verifies.
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        permalink = f"{C.FB_BASE}/me/posts/555000"
        browser = FakeFBBrowser(permalink=permalink)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "hello", [])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_post_id"] == "555000"
        assert ("click_css", C.POST_BUTTON_CSS_CANDIDATES[0]) in browser.calls


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE — target resolution + persistence + isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestService:
    async def test_prepare_persists_pending_with_target(self):
        browser = FakeFBBrowser(target_type=C.TARGET_PAGE)
        svc, repo = _service(
            browser,
            target={"target_type": C.TARGET_PAGE, "target_identifier": "pg", "target_name": "Pg"},
        )
        out = await svc.prepare("t1", "acc1", "op1", "cap", [])
        assert out["status"] == "action_required"
        assert out["action"] == C.CONFIRM_ACTION
        assert out["preview"]["target_type"] == "page"
        row = await repo.find(out["confirmation_id"], "t1")
        assert row is not None and row.platform == "facebook"
        # target_type persisted via title, identifier via subreddit (reused cols).
        assert row.title == "page" and row.subreddit == "pg"

    async def test_cancel_without_publish(self):
        browser = FakeFBBrowser()
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [])
        res = await svc.cancel("t1", out["confirmation_id"])
        assert res["status"] == "cancelled"
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "cancelled"

    async def test_list_pending_only_facebook_and_tenant_scoped(self):
        browser = FakeFBBrowser()
        svc, repo = _service(browser)
        await svc.prepare("t1", "acc1", "op1", "cap", [])
        await svc.prepare("t2", "acc9", "op2", "cap", [])
        await repo.create("t1", "acc1", "opR", "reddit", "hermes",
                          title="t", body="b", subreddit="test", media_paths=[])
        t1 = await svc.list_pending("t1")
        t2 = await svc.list_pending("t2")
        assert len(t1) == 1 and t1[0]["operation_id"] == "op1" and t1[0]["platform"] == "facebook"
        assert len(t2) == 1 and t2[0]["operation_id"] == "op2"

    async def test_confirm_cross_tenant_not_found(self):
        browser = FakeFBBrowser()
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [])
        res = await svc.confirm("t2", out["confirmation_id"])
        assert res["status"] == "not_found"
        assert not browser.clicked_post()

    def test_profile_key_isolated_and_namespaced(self):
        svc = FacebookUserAssistedService(
            pending_repo=FakePendingRepo(), workflow=HermesFacebookWorkflow(),
            adapter_factory=lambda k: None,
            target_resolver=lambda a, t: _async_return({}),
        )
        k1 = svc._profile_key("t1", "acc1")
        k2 = svc._profile_key("t1", "acc2")
        k3 = svc._profile_key("t2", "acc1")
        assert k1 != k2 and k1 != k3
        assert k1.startswith("fb_")

    async def test_override_target_type_takes_precedence(self):
        # Account resolves to profile, but an explicit page override wins.
        browser = FakeFBBrowser(target_type=C.TARGET_PAGE)
        svc, repo = _service(
            browser,
            target={"target_type": C.TARGET_PROFILE, "target_identifier": "", "target_name": "Me"},
        )
        out = await svc.prepare("t1", "acc1", "op1", "hi", [],
                                target_type="page", target_identifier="my-page")
        assert out["preview"]["target_type"] == "page"


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULED-JOB PATH + FLAG + PERMALINK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduledPathAndFlag:
    async def test_execute_with_non_semantic_adapter_action_required(self):
        class Coarse:
            pass
        res = await HermesFacebookWorkflow().execute(_instruction(), Coarse())
        assert res.status == ExternalResultStatus.ACTION_REQUIRED

    def test_flag_defaults_false(self, monkeypatch):
        import app.config as c
        monkeypatch.delenv("ENABLE_HERMES_FACEBOOK", raising=False)
        assert c._bool_env("ENABLE_HERMES_FACEBOOK", False) is False

    def test_routes_404_when_flag_disabled(self, monkeypatch):
        import app.config as c
        from fastapi import HTTPException
        from app.social_publishing.api import facebook_routes
        monkeypatch.setattr(c, "ENABLE_HERMES_PROVIDER", True, raising=False)
        monkeypatch.setattr(c, "ENABLE_HERMES_FACEBOOK", False, raising=False)
        with pytest.raises(HTTPException) as e:
            facebook_routes._require_flag()
        assert e.value.status_code == 404


class TestPermalinkHelpers:
    def test_is_facebook_permalink(self):
        assert _is_facebook_permalink(f"{C.FB_BASE}/me/posts/1") is True
        assert _is_facebook_permalink(f"{C.FB_BASE}/permalink.php?story_fbid=9&id=5") is True
        assert _is_facebook_permalink("https://evil.com/posts/1") is False
        assert _is_facebook_permalink(f"{C.FB_BASE}/somepage") is False   # not a permalink
        assert _is_facebook_permalink("/me/posts/1") is False             # relative

    def test_permalink_id(self):
        assert _permalink_id(f"{C.FB_BASE}/me/posts/123") == "123"
        assert _permalink_id(f"{C.FB_BASE}/me/permalink/456") == "456"
        assert _permalink_id(f"{C.FB_BASE}/permalink.php?story_fbid=789&id=5") == "789"
        assert _permalink_id("") is None
