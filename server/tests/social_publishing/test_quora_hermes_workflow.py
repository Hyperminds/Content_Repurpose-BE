"""Quora user-assisted Hermes workflow tests.

A scripted fake SemanticBrowser + in-memory pending repo drive the two-phase
(prepare → confirm) flow deterministically — no real browser, no Playwright, no
network, no database.

Quora specifics:
  - text-only is allowed; image is OPTIONAL (at most one).
  - success signal is composer-closed and/or a permalink/toast.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
)
from app.social_publishing.providers.hermes.quora import constants as C
from app.social_publishing.providers.hermes.quora.validation import validate_post
from app.social_publishing.providers.hermes.quora.workflow import (
    HermesQuoraWorkflow,
    _is_quora_permalink,
    _permalink_id,
)
from app.social_publishing.providers.hermes.quora.service import QuoraUserAssistedService
from app.social_publishing.providers.hermes.semantic_browser import (
    BrowserError,
    BrowserTimeout,
    SemanticBrowser,
)


# ══════════════════════════════════════════════════════════════════════════════
# Scripted fake SemanticBrowser tailored to the Quora flow
# ══════════════════════════════════════════════════════════════════════════════

def _default_visible():
    vis = set()
    vis.update(C.LOC_OPEN_COMPOSER_CANDIDATES[:1])  # composer-entry (auth signal)
    vis.update(C.LOC_TEXT_EDITOR_CANDIDATES[:1])
    vis.update(C.LOC_POST_BUTTON_CANDIDATES[:1])
    return vis


class FakeQuoraBrowser:
    def __init__(
        self,
        *,
        authenticated=True,
        attached=1,
        bg_preview=0,
        cdn_preview=0,
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
        self._attached = attached
        self._bg_preview = bg_preview
        # Count returned for the Quora-CDN sizeable preview image (count_large_images).
        self._cdn_preview = cdn_preview
        self._file_input_present = file_input_present
        self.permalink = permalink
        self.published_confirmation = published_confirmation
        self.raise_on = raise_on
        self.error = error or BrowserError("scripted failure")
        self._visible = set(visible) if visible is not None else _default_visible()
        self._url = C.HOME_URL
        self.calls = []
        self.started = False
        self.closed = False
        self._posted = False
        self._composer_open_flag = False
        self._composer_opens = composer_opens
        self._post_css_matches = set(
            post_css_matches if post_css_matches is not None
            else {C.POST_BUTTON_CSS_CANDIDATES[0]}
        )
        self._closes_on_post = closes_on_post

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
        if (role, name) in set(C.LOC_OPEN_COMPOSER_CANDIDATES) and self._composer_opens:
            self._composer_open_flag = True
        if (role, name) in set(C.LOC_POST_BUTTON_CANDIDATES):
            self._posted = True
            if self._closes_on_post:
                self._composer_open_flag = False

    async def set_input_files(self, label, file_paths, *, timeout_s=None):
        self.calls.append(("set_input_files", label, tuple(file_paths)))
        self._maybe_raise("set_input_files")

    async def count_attached_files(self, selector=C.IMAGE_FILE_INPUT_SELECTOR, *, timeout_s=None):
        self.calls.append(("count_attached_files", selector))
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
        # The composer's Post button (puppeteer_test_modal_submit) is present
        # while the composer is open and disappears once the post is accepted —
        # the signal the verification poller uses to detect a successful publish.
        if selector in set(C.POST_BUTTON_CSS_CANDIDATES):
            return self._composer_open_flag
        return False

    async def click_css(self, selector, *, timeout_s=None):
        self.calls.append(("click_css", selector))
        self._maybe_raise("click_css")
        if selector in self._post_css_matches:
            self._posted = True
            if self._closes_on_post:
                self._composer_open_flag = False
            return True
        return False

    async def fill_css(self, selector, value, *, timeout_s=None):
        self.calls.append(("fill_css", selector, value))
        self._maybe_raise("fill_css")
        # The dialog-scoped editor selectors accept text only while open.
        if selector in set(C.TEXT_EDITOR_CSS_CANDIDATES) and self._composer_open_flag:
            return True
        return False

    async def clear_css(self, selector, *, timeout_s=None):
        self.calls.append(("clear_css", selector))
        self._maybe_raise("clear_css")
        return selector in set(C.TEXT_EDITOR_CSS_CANDIDATES) and self._composer_open_flag

    async def count_large_images(self, selector, min_px, *, timeout_s=None):
        self.calls.append(("count_large_images", selector, min_px))
        # Model Quora's CDN preview: a sizeable composer image appears once the
        # image is attached (driven by the same `_attached` knob).
        if selector == C.IMAGE_PREVIEW_CDN_SELECTOR:
            return self._cdn_preview
        return 0

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


def _instruction(text="Hello Quora", media_paths=None, op="op1", tenant="t1", account="acc1"):
    return PublishInstruction(
        operation_id=op, tenant_id=tenant, account_id=account,
        platform=C.PLATFORM, provider_name="hermes", text=text,
        meta={"quora_text": text, "quora_media_paths": media_paths or [], "quora_username": "tester"},
    )


def _real_image(tmp_path, name="pic.jpg", size=128):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff" + b"0" * size)
    return str(p)


def _service(browser, pending=None):
    repo = pending or FakePendingRepo()
    svc = QuoraUserAssistedService(
        pending_repo=repo,
        workflow=HermesQuoraWorkflow(),
        adapter_factory=lambda profile_key: browser,
        username_resolver=lambda account_id, tenant_id: _async_return("tester"),
    )
    return svc, repo


def _async_return(value):
    async def _c():
        return value
    return _c()


# ══════════════════════════════════════════════════════════════════════════════
# ENUM / CAPABILITIES / ROUTING / FLAG
# ══════════════════════════════════════════════════════════════════════════════

class TestRegistrationAndRouting:
    def test_platform_enum_exists(self):
        from app.social_publishing.domain.enums import SocialPlatform
        assert SocialPlatform.QUORA.value == "quora"

    def test_platform_and_capabilities(self):
        wf = HermesQuoraWorkflow()
        assert wf.platform == C.PLATFORM == "quora"
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
        prov.register_workflow(HermesQuoraWorkflow())
        assert C.PLATFORM in getattr(prov, "_workflows", {})
        assert prov.capabilities(C.PLATFORM).text is True

    def test_router_quora_user_assisted(self):
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
        hermes.register_workflow(HermesQuoraWorkflow())
        router = ProviderRouter()
        router.register(hermes)

        hermes_acct = SocialAccount(
            id="a2", tenant_id="t1", platform=SocialPlatform.QUORA,
            account_name="n", platform_account_id="PA2",
            provider_type=ProviderType.USER_ASSISTED_AGENT, provider_name=ProviderName.HERMES.value,
        )
        assert router.resolve(hermes_acct) is hermes

    def test_flag_defaults_false(self, monkeypatch):
        import app.config as c
        monkeypatch.delenv("ENABLE_HERMES_QUORA", raising=False)
        assert c._bool_env("ENABLE_HERMES_QUORA", False) is False

    def test_routes_404_when_flag_disabled(self, monkeypatch):
        import app.config as c
        from fastapi import HTTPException
        from app.social_publishing.api import quora_routes
        monkeypatch.setattr(c, "ENABLE_HERMES_PROVIDER", True, raising=False)
        monkeypatch.setattr(c, "ENABLE_HERMES_QUORA", False, raising=False)
        with pytest.raises(HTTPException) as e:
            quora_routes._require_flag()
        assert e.value.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation:
    def test_text_only_valid(self):
        parsed, errors = validate_post("hello", [])
        assert errors == [] and parsed.text == "hello" and parsed.media_paths == []

    def test_image_plus_text_valid(self):
        parsed, errors = validate_post("hi", ["a.jpg"])
        assert errors == [] and parsed.media_paths == ["a.jpg"]

    def test_image_only_valid(self):
        parsed, errors = validate_post("", ["a.png"])
        assert errors == [] and parsed.text == ""

    def test_empty_post_rejected(self):
        parsed, errors = validate_post("", [])
        assert parsed is None and any("text or an image" in e.lower() for e in errors)

    def test_multiple_images_rejected(self):
        parsed, errors = validate_post("x", ["a.jpg", "b.jpg"])
        assert parsed is None and any("at most" in e.lower() for e in errors)

    def test_unsupported_type_rejected(self):
        parsed, errors = validate_post("x", ["clip.mp4"])
        assert parsed is None and any("mp4" in e.lower() or "unsupported" in e.lower() for e in errors)

    def test_text_too_long_rejected(self):
        parsed, errors = validate_post("x" * (C.MAX_TEXT_LENGTH + 1), [])
        assert parsed is None and any("exceeds" in e.lower() for e in errors)


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthentication:
    async def test_not_authenticated_requires_action(self):
        browser = FakeQuoraBrowser(authenticated=False)
        res = await HermesQuoraWorkflow().prepare(_instruction(), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert res.error_code == ExternalErrorCode.ACTION_REQUIRED
        assert ("goto", C.LOGIN_URL) in browser.calls
        assert not browser.clicked_post()


# ══════════════════════════════════════════════════════════════════════════════
# PREPARE
# ══════════════════════════════════════════════════════════════════════════════

class TestPreparation:
    async def test_text_only_reaches_action_required(self):
        browser = FakeQuoraBrowser()
        res = await HermesQuoraWorkflow().prepare(_instruction(text="hello quora"), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert ("goto", C.HOME_URL) in browser.calls
        assert any(c[0] in ("fill", "fill_css") for c in browser.calls)
        assert not any(c[0] == "set_input_files" for c in browser.calls)  # no image
        assert not browser.clicked_post()

    async def test_image_plus_text(self, tmp_path):
        browser = FakeQuoraBrowser(attached=1)
        res = await HermesQuoraWorkflow().prepare(
            _instruction(text="with image", media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert any(c[0] == "set_input_files" for c in browser.calls)
        assert not browser.clicked_post()

    async def test_image_attach_never_clicks_add_image_button(self, tmp_path):
        # Image set DIRECTLY on the hidden input; "Add image" is never clicked
        # (clicking it opens the OS file picker).
        browser = FakeQuoraBrowser(attached=1)
        res = await HermesQuoraWorkflow().prepare(
            _instruction(text="x", media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert any(c[0] == "set_input_files" for c in browser.calls)
        assert not any(
            c[0] == "click" and (c[1], c[2]) in set(C.LOC_ADD_IMAGE_CANDIDATES)
            for c in browser.calls if len(c) >= 3
        )

    async def test_text_entry_clears_editor_before_typing(self):
        # Quora persists draft text; the editor must be CLEARED (select-all+delete)
        # before typing so a leftover draft can't be appended to the new post.
        browser = FakeQuoraBrowser()
        res = await HermesQuoraWorkflow().prepare(_instruction(text="fresh text"), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        clear_idx = next((i for i, c in enumerate(browser.calls)
                          if c[0] == "clear_css" and c[1] in set(C.TEXT_EDITOR_CSS_CANDIDATES)), None)
        fill_idx = next((i for i, c in enumerate(browser.calls)
                         if c[0] == "fill_css" and c[1] in set(C.TEXT_EDITOR_CSS_CANDIDATES)), None)
        assert clear_idx is not None, "editor was not cleared"
        assert fill_idx is not None, "text was not filled"
        assert clear_idx < fill_idx, "editor must be cleared BEFORE filling"

    async def test_image_attach_via_quora_cdn_preview(self, tmp_path):
        # Quora uploads to its CDN on attach (no blob: preview). The sizeable
        # CDN <img> is the real attach signal; attach must succeed on it alone.
        browser = FakeQuoraBrowser(attached=0, bg_preview=0, cdn_preview=1)
        res = await HermesQuoraWorkflow().prepare(
            _instruction(text="with image", media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert any(c[0] == "set_input_files" for c in browser.calls)
        # It attached to the IMAGE-accepting input selector (not the video one).
        assert any(
            c[0] == "set_input_files" and c[1] == C.IMAGE_FILE_INPUT_SELECTOR
            for c in browser.calls if len(c) >= 2
        )
        assert not browser.clicked_post()

    async def test_image_attach_failure_fails_safe(self, tmp_path):
        # No preview of ANY kind (no blob, no bg, no CDN) → attach treated as failed.
        browser = FakeQuoraBrowser(attached=0, bg_preview=0, cdn_preview=0)
        res = await HermesQuoraWorkflow().prepare(
            _instruction(text="x", media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert not browser.clicked_post()

    async def test_composer_never_opens_fails(self):
        browser = FakeQuoraBrowser(composer_opens=False)
        browser._visible = set(C.LOC_OPEN_COMPOSER_CANDIDATES[:1])
        res = await HermesQuoraWorkflow().prepare(_instruction(text="hello"), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert "composer" in (res.error_message or "").lower()
        assert not browser.clicked_post()

    async def test_clean_slate_discards_stale_composer_before_opening(self):
        # A leftover/draft composer is open at session start. prepare must discard
        # it (close/discard + fresh home navigation) BEFORE opening a new one, so
        # stale text/image can't leak into the post.
        browser = FakeQuoraBrowser()
        browser._composer_open_flag = True   # simulate a stale open composer
        # Model the composer's visible Close control so the discard click fires.
        browser._visible = browser._visible | {C.LOC_COMPOSER_CLOSE_CANDIDATES[0]}
        res = await HermesQuoraWorkflow().prepare(_instruction(text="fresh post"), browser)
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        # It attempted to close the stale composer...
        assert any(
            c[0] == "click" and (c[1], c[2]) in set(C.LOC_COMPOSER_CLOSE_CANDIDATES)
            for c in browser.calls if len(c) >= 3
        )
        # ...and re-navigated to a clean home page before composing (>= 2 gotos:
        # the auth nav + the clean-slate nav).
        assert sum(1 for c in browser.calls if c[0] == "goto" and c[1] == C.HOME_URL) >= 2
        assert not browser.clicked_post()

    async def test_empty_post_fails_before_browser(self):
        browser = FakeQuoraBrowser()
        res = await HermesQuoraWorkflow().prepare(_instruction(text="", media_paths=[]), browser)
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []

    async def test_missing_image_on_disk_fails(self):
        browser = FakeQuoraBrowser()
        res = await HermesQuoraWorkflow().prepare(
            _instruction(text="x", media_paths=["/no/such/file.jpg"]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []


# ══════════════════════════════════════════════════════════════════════════════
# CONFIRM / PUBLISH / VERIFY
# ══════════════════════════════════════════════════════════════════════════════

class TestConfirmPublish:
    async def test_confirm_published_via_permalink(self, monkeypatch):
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        permalink = f"{C.QUORA_BASE}/profile/tester/some-post-slug"
        browser = FakeQuoraBrowser(permalink=permalink)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "hello", [])
        assert out["status"] == "action_required"
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_url"] == permalink
        assert browser.clicked_post()
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "published"

    async def test_confirm_published_via_composer_closed_no_permalink(self, monkeypatch):
        # Reliable signal: composer closes after Post, no permalink → PUBLISHED.
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        browser = FakeQuoraBrowser(permalink=None, published_confirmation=False, closes_on_post=True)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "hello world", [])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_url"] is None
        assert browser.clicked_post()

    async def test_confirm_unknown_when_no_signal(self, monkeypatch):
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "POST_POLL_MAX_CHECKS", 2)
        browser = FakeQuoraBrowser(permalink=None, published_confirmation=False, closes_on_post=False)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "hello", [])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "unknown"
        assert browser.clicked_post()   # Post WAS clicked (post may exist)
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "unknown"

    async def test_verify_never_clicks_post(self, monkeypatch):
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "POST_POLL_MAX_CHECKS", 2)
        browser = FakeQuoraBrowser(permalink=None, closes_on_post=False)
        await HermesQuoraWorkflow().verify(_instruction(), browser)
        assert not browser.clicked_post()


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE — persistence, ownership, duplicate prevention, isolation, no secrets
# ══════════════════════════════════════════════════════════════════════════════

class TestService:
    async def test_prepare_persists_pending(self):
        browser = FakeQuoraBrowser()
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [])
        assert out["status"] == "action_required"
        assert out["action"] == C.CONFIRM_ACTION
        assert out["preview"]["media_count"] == 0
        row = await repo.find(out["confirmation_id"], "t1")
        assert row is not None and row.status == "action_required" and row.platform == "quora"

    async def test_confirm_cross_tenant_not_found(self):
        browser = FakeQuoraBrowser()
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [])
        res = await svc.confirm("t2", out["confirmation_id"])
        assert res["status"] == "not_found"
        assert not browser.clicked_post()

    async def test_duplicate_confirm_prevented(self, monkeypatch):
        # Second confirm after a successful publish must NOT publish again.
        monkeypatch.setattr(C, "POST_POLL_INTERVAL_S", 0)
        browser = FakeQuoraBrowser(permalink=None, closes_on_post=True)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [])
        first = await svc.confirm("t1", out["confirmation_id"])
        assert first["status"] == "published"
        # Reset the click-tracking to detect any new click on the 2nd confirm.
        browser.calls.clear()
        browser._posted = False
        second = await svc.confirm("t1", out["confirmation_id"])
        assert second["status"] == "published"   # terminal status returned
        assert "No longer awaiting confirmation" in (second.get("message") or "")
        assert not browser.clicked_post()         # NO second publish

    async def test_cancel_without_publish(self):
        browser = FakeQuoraBrowser()
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [])
        res = await svc.cancel("t1", out["confirmation_id"])
        assert res["status"] == "cancelled"
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "cancelled"

    async def test_list_pending_only_quora_and_tenant_scoped(self):
        browser = FakeQuoraBrowser()
        svc, repo = _service(browser)
        await svc.prepare("t1", "acc1", "op1", "cap", [])
        await svc.prepare("t2", "acc9", "op2", "cap", [])
        await repo.create("t1", "acc1", "opR", "reddit", "hermes",
                          title="t", body="b", subreddit="test", media_paths=[])
        t1 = await svc.list_pending("t1")
        t2 = await svc.list_pending("t2")
        assert len(t1) == 1 and t1[0]["operation_id"] == "op1" and t1[0]["platform"] == "quora"
        assert len(t2) == 1 and t2[0]["operation_id"] == "op2"

    def test_profile_key_isolated_and_namespaced(self):
        svc = QuoraUserAssistedService(
            pending_repo=FakePendingRepo(), workflow=HermesQuoraWorkflow(),
            adapter_factory=lambda k: None,
            username_resolver=lambda a, t: _async_return(""),
        )
        k1 = svc._profile_key("t1", "acc1")
        k2 = svc._profile_key("t1", "acc2")
        k3 = svc._profile_key("t2", "acc1")
        assert k1 != k2 and k1 != k3
        assert k1.startswith("quora_")

    async def test_no_secret_fields_in_pending_model(self):
        import dataclasses
        from app.social_publishing.domain.models import PendingConfirmation
        fields = {f.name for f in dataclasses.fields(PendingConfirmation)}
        for forbidden in ("password", "cookie", "cookies", "session",
                          "access_token", "token", "credentials", "secret"):
            assert forbidden not in fields


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULED-JOB PATH + PERMALINK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduledPathAndHelpers:
    async def test_execute_with_non_semantic_adapter_action_required(self):
        class Coarse:
            pass
        res = await HermesQuoraWorkflow().execute(_instruction(), Coarse())
        assert res.status == ExternalResultStatus.ACTION_REQUIRED

    def test_is_quora_permalink(self):
        assert _is_quora_permalink(f"{C.QUORA_BASE}/profile/tester/my-post") is True
        assert _is_quora_permalink("https://evil.com/profile/x/y") is False
        assert _is_quora_permalink(f"{C.QUORA_BASE}/") is False   # home, not a post
        assert _is_quora_permalink("/profile/x/y") is False       # relative

    def test_permalink_id(self):
        assert _permalink_id(f"{C.QUORA_BASE}/profile/tester/my-post-slug") == "my-post-slug"
        assert _permalink_id("") is None
