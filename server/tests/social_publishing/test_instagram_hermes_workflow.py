"""Instagram user-assisted Hermes workflow tests.

Mirrors the Reddit workflow tests: a scripted fake SemanticBrowser + in-memory
pending-confirmation repo drive the two-phase (prepare → confirm) flow
deterministically — no real browser, no Playwright, no network, no database.

Instagram specifics vs Reddit:
  - image is REQUIRED (no text-only post), caption optional
  - locators are candidate-lists probed via is_visible/click
  - success signal is a "post shared" confirmation and/or a /p/<code> permalink
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
)
from app.social_publishing.providers.hermes.instagram import constants as C
from app.social_publishing.providers.hermes.instagram.validation import validate_post
from app.social_publishing.providers.hermes.instagram.workflow import (
    HermesInstagramWorkflow,
    _is_instagram_permalink,
    _permalink_id,
)
from app.social_publishing.providers.hermes.instagram.service import InstagramUserAssistedService
from app.social_publishing.providers.hermes.semantic_browser import (
    BrowserError,
    BrowserTimeout,
    SemanticBrowser,
)


# ══════════════════════════════════════════════════════════════════════════════
# Scripted fake SemanticBrowser tailored to the Instagram flow
# ══════════════════════════════════════════════════════════════════════════════

# The full set of locators a healthy authenticated composer exposes across the
# prepare flow. Tests start from this and remove/override to script failures.
def _default_visible():
    # Composer-open is modelled via click_svg_anchor("Post") -> is_css_visible
    # (the confirmed dialog signal), NOT via pre-seeded heading visibility. So we
    # seed only the auth signal and the later in-composer controls (Next/caption/
    # Share) that the workflow checks AFTER the composer is open.
    vis = set()
    for group in (
        C.LOC_NEXT_CANDIDATES[:1],
        C.LOC_CAPTION_CANDIDATES[:1],
        C.LOC_SHARE_CANDIDATES[:1],
        C.LOC_AUTH_SIGNAL_CANDIDATES[:1],  # authenticated signal (sidebar create)
    ):
        vis.update(group)
    return vis


class FakeIGBrowser:
    def __init__(
        self,
        *,
        authenticated=True,
        attached=1,
        bg_preview=0,
        file_input_present=1,
        permalink=None,
        shared_confirmation=False,
        raise_on=None,
        error=None,
        visible=None,
        profile_permalinks=None,
        svg_anchors=None,
        composer_opens=True,
    ):
        self.authenticated = authenticated
        self._attached = attached
        # Count returned for the blob-backed CSS background preview selector
        # (the current composer renders the crop as background-image: url(blob:…)
        # rather than an <img>). Independent of _attached so a test can model a
        # bg-only preview (img=0, bg=1).
        self._bg_preview = bg_preview
        # Whether the composer's hidden <input type=file> has mounted.
        self._file_input_present = file_input_present
        self.permalink = permalink
        self.shared_confirmation = shared_confirmation
        self.raise_on = raise_on
        self.error = error or BrowserError("scripted failure")
        self._visible = set(visible) if visible is not None else _default_visible()
        self.profile_permalinks = list(profile_permalinks or [])
        self._url = C.HOME_URL
        self.calls = []
        self.started = False
        self.closed = False
        # After Share is clicked, the confirmation/permalink becomes observable.
        self._shared = False
        # Confirmed open-composer path modelling:
        #  - which svg[aria-label=...] anchors are clickable (New post / Post)
        #  - whether the composer dialog is "open" (drives is_css_visible + the
        #    Create-new-post heading). Default: both svg anchors present, and the
        #    composer becomes open once the flyout "Post" anchor is clicked.
        if svg_anchors is None:
            svg_anchors = {C.NEW_POST_SVG_LABEL, C.POST_FLYOUT_SVG_LABEL}
        self._svg_anchors = set(svg_anchors)
        self._composer_open_after_post = composer_opens
        self._composer_dialog_open = False

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
        # The permalink only becomes the address after Share was clicked.
        if self._shared and self.permalink:
            return self.permalink
        return self._url

    async def is_visible(self, role, name, *, timeout_s=None):
        self.calls.append(("is_visible", role, name))
        self._maybe_raise("is_visible")
        # Login-form signal: visible only when NOT authenticated.
        if (role, name) in set(C.LOC_LOGIN_SIGNAL_CANDIDATES):
            return not self.authenticated
        # Auth/create signal requires authentication.
        if (role, name) in set(C.LOC_AUTH_SIGNAL_CANDIDATES) and not self.authenticated:
            return False
        # Shared-confirmation text only appears after Share clicked.
        if (role, name) in set(C.POST_SHARED_TEXT_CANDIDATES):
            return self._shared and self.shared_confirmation
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
        if (role, name) in set(C.LOC_SHARE_CANDIDATES):
            self._shared = True

    async def set_input_files(self, label, file_paths, *, timeout_s=None):
        self.calls.append(("set_input_files", label, tuple(file_paths)))
        self._maybe_raise("set_input_files")

    async def count_attached_files(self, selector=C.IMAGE_PREVIEW_SELECTOR, *, timeout_s=None):
        self.calls.append(("count_attached_files", selector))
        # The file-input selector reports whether the (hidden) uploader mounted;
        # the preview selector reports whether media actually attached.
        if selector == C.IMAGE_FILE_INPUT_SELECTOR:
            return self._file_input_present
        # Blob-backed CSS background preview — separate knob so a test can model
        # the live case where the <img> preview is absent but the bg preview is
        # present.
        if selector == C.IMAGE_PREVIEW_BG_SELECTOR:
            return self._bg_preview
        return self._attached

    async def find_post_permalinks(self, title, *, timeout_s=None):
        self.calls.append(("find_post_permalinks", title))
        return list(self.profile_permalinks)

    async def click_svg_anchor(self, svg_aria_label, *, timeout_s=None):
        self.calls.append(("click_svg_anchor", svg_aria_label))
        self._maybe_raise("click_svg_anchor")
        if svg_aria_label not in self._svg_anchors:
            return False
        # Clicking the flyout "Post" anchor opens the composer (if configured).
        if svg_aria_label == C.POST_FLYOUT_SVG_LABEL and self._composer_open_after_post:
            self._composer_dialog_open = True
        return True

    async def is_css_visible(self, selector, *, timeout_s=None):
        self.calls.append(("is_css_visible", selector))
        self._maybe_raise("is_css_visible")
        if selector in (C.COMPOSER_DIALOG_SELECTOR, C.COMPOSER_DIALOG_SELECTOR_FALLBACK):
            return self._composer_dialog_open
        return False

    async def screenshot(self, path):
        self.calls.append(("screenshot", path))

    # helpers
    def clicked_share(self):
        return any(c[0] == "click" and (c[1], c[2]) in set(C.LOC_SHARE_CANDIDATES)
                   for c in self.calls if len(c) >= 3)


# ══════════════════════════════════════════════════════════════════════════════
# In-memory pending repo (reused shape from Reddit tests)
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
                row.update(body=body, media_paths=list(media_paths or []),
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
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch):
    """Keep the workflow's bounded polls instant/short in ALL tests (file-input
    wait + share verification). With the scripted fake there is no real page."""
    monkeypatch.setattr(C, "SHARE_POLL_INTERVAL_S", 0, raising=False)
    monkeypatch.setattr(C, "SHARE_POLL_MAX_CHECKS", 3, raising=False)


@pytest.fixture(autouse=True)
def _clear_live_session_cache():
    """The live-session cache is a process-wide singleton; clear it between
    tests so a kept-alive adapter from one test can't bleed into another."""
    from app.social_publishing.providers.hermes.live_session_cache import live_session_cache
    live_session_cache._by_confirmation.clear()
    yield
    live_session_cache._by_confirmation.clear()


def _instruction(caption="Nice pic", media_paths=None, op="op1", tenant="t1",
                 account="acc1", username="tester"):
    return PublishInstruction(
        operation_id=op, tenant_id=tenant, account_id=account,
        platform=C.PLATFORM, provider_name="hermes", text=caption,
        meta={
            "instagram_caption": caption,
            "instagram_media_paths": media_paths or ["/tmp/a.jpg"],
            "instagram_username": username,
        },
    )


def _real_image(tmp_path, name="pic.jpg", size=128):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff" + b"0" * size)  # jpeg-ish header
    return str(p)


def _service(browser, pending=None):
    repo = pending or FakePendingRepo()
    svc = InstagramUserAssistedService(
        pending_repo=repo,
        workflow=HermesInstagramWorkflow(),
        adapter_factory=lambda profile_key: browser,
        username_resolver=lambda account_id, tenant_id: _async_return("tester"),
    )
    return svc, repo


def _async_return(value):
    async def _c():
        return value
    return _c()


# ══════════════════════════════════════════════════════════════════════════════
# CAPABILITIES / REGISTRATION / ROUTING
# ══════════════════════════════════════════════════════════════════════════════

class TestRegistrationAndRouting:
    def test_platform_and_capabilities(self):
        wf = HermesInstagramWorkflow()
        assert wf.platform == C.PLATFORM == "instagram"
        caps = wf.capabilities()
        assert isinstance(caps, ProviderCapabilities)
        assert caps.image is True
        assert caps.text is False          # Instagram requires media
        assert caps.video is False         # no reels/video this phase
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
        prov.register_workflow(HermesInstagramWorkflow())
        assert C.PLATFORM in getattr(prov, "_workflows", {})
        assert prov.capabilities(C.PLATFORM).image is True

    def test_router_instagram_native_vs_user_assisted(self):
        # Native Instagram routes to the native provider; user-assisted routes to
        # the Hermes provider. Same platform, different provider_type.
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
        hermes.register_workflow(HermesInstagramWorkflow())

        class _FakeNative:
            provider_name = ProviderName.META.value
            provider_type = ProviderType.NATIVE_API

        router = ProviderRouter()
        router.register(_FakeNative())
        router.register(hermes)

        native_acct = SocialAccount(
            id="a1", tenant_id="t1", platform=SocialPlatform.INSTAGRAM,
            account_name="n", platform_account_id="PA1",
            provider_type=ProviderType.NATIVE_API, provider_name=ProviderName.META.value,
        )
        hermes_acct = SocialAccount(
            id="a2", tenant_id="t1", platform=SocialPlatform.INSTAGRAM,
            account_name="n", platform_account_id="PA2",
            provider_type=ProviderType.USER_ASSISTED_AGENT, provider_name=ProviderName.HERMES.value,
        )
        assert router.resolve(native_acct) is not hermes       # native → native provider
        assert router.resolve(hermes_acct) is hermes            # user-assisted → hermes


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation:
    def test_image_required(self):
        parsed, errors = validate_post("caption", [])
        assert parsed is None and any("image is required" in e.lower() for e in errors)

    def test_single_image_only(self):
        parsed, errors = validate_post("c", ["a.jpg", "b.jpg"])
        assert parsed is None and any("at most" in e.lower() for e in errors)

    def test_unsupported_type(self):
        parsed, errors = validate_post("c", ["clip.mp4"])
        assert parsed is None and any("mp4" in e.lower() or "unsupported" in e.lower() for e in errors)

    def test_caption_too_long(self):
        parsed, errors = validate_post("x" * (C.MAX_CAPTION_LENGTH + 1), ["a.jpg"])
        assert parsed is None and any("caption" in e.lower() for e in errors)

    def test_valid_single_image(self):
        parsed, errors = validate_post("hi", ["a.jpg"])
        assert errors == [] and parsed.media_paths == ["a.jpg"] and parsed.caption == "hi"

    def test_empty_caption_ok_with_image(self):
        parsed, errors = validate_post("", ["a.png"])
        assert errors == [] and parsed.caption == ""


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthentication:
    async def test_not_authenticated_requires_action(self, tmp_path):
        browser = FakeIGBrowser(authenticated=False)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert res.error_code == ExternalErrorCode.ACTION_REQUIRED
        assert ("goto", C.LOGIN_URL) in browser.calls
        assert not browser.clicked_share()


# ══════════════════════════════════════════════════════════════════════════════
# PREPARE
# ══════════════════════════════════════════════════════════════════════════════

class TestPreparation:
    async def test_prepare_reaches_action_required_no_share(self, tmp_path):
        browser = FakeIGBrowser(authenticated=True, attached=1)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(caption="hello", media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        # It opened the composer, attached, filled caption, verified Share — no click.
        assert any(c[0] == "set_input_files" for c in browser.calls)
        assert ("count_attached_files", C.IMAGE_PREVIEW_SELECTOR) in browser.calls
        assert any(c[0] == "fill" for c in browser.calls)
        assert not browser.clicked_share()

    async def test_prepare_missing_image_fails_before_browser(self):
        browser = FakeIGBrowser(authenticated=True)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=[]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []

    async def test_prepare_missing_file_on_disk_fails(self):
        browser = FakeIGBrowser(authenticated=True)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=["/no/such/file.jpg"]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert res.error_code == ExternalErrorCode.EXTERNAL_CONTENT_REJECTED
        assert browser.calls == []

    async def test_prepare_unsupported_type_fails(self):
        browser = FakeIGBrowser(authenticated=True)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=["clip.mp4"]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert browser.calls == []

    async def test_prepare_attach_failure_fails_safe(self, tmp_path):
        # No attach signal at all: no <img> preview (attached=0), no blob-backed
        # background preview (bg_preview=0), AND no visible Next button. All three
        # _image_attached() signals absent → attach is treated as failed.
        browser = FakeIGBrowser(authenticated=True, attached=0, bg_preview=0)
        browser._visible = browser._visible - set(C.LOC_NEXT_CANDIDATES)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert not browser.clicked_share()

    async def test_prepare_uploader_never_mounts_fails_safe(self, tmp_path):
        # The composer MODAL opens (dialog title visible) but the hidden file
        # input never appears → FAILED, and we never attempt to attach.
        browser = FakeIGBrowser(authenticated=True, file_input_present=0)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert not any(c[0] == "set_input_files" for c in browser.calls)
        assert not browser.clicked_share()

    async def test_prepare_composer_never_opens_fails(self, tmp_path):
        # The New Post + Post flyout clicks happen, but the composer modal never
        # opens (composer_opens=False) → FAILED at the open step, before attach.
        browser = FakeIGBrowser(
            authenticated=True, file_input_present=0, composer_opens=False,
        )
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert "composer" in (res.error_message or "").lower()
        assert not any(c[0] == "set_input_files" for c in browser.calls)
        assert not browser.clicked_share()

    async def test_prepare_opens_composer_via_confirmed_svg_anchor_path(self, tmp_path):
        # Verifies the CONFIRMED flow: New Post svg-anchor clicked, then the
        # "Post" flyout svg-anchor clicked (svg.closest(a[href='#'])), composer
        # verified open before attaching. No generic get_by_role('link','Post').
        browser = FakeIGBrowser(authenticated=True, attached=1, file_input_present=1)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        assert ("click_svg_anchor", C.NEW_POST_SVG_LABEL) in browser.calls
        assert ("click_svg_anchor", C.POST_FLYOUT_SVG_LABEL) in browser.calls
        # Composer verified via the confirmed dialog signal.
        assert ("is_css_visible", C.COMPOSER_DIALOG_SELECTOR) in browser.calls
        assert not browser.clicked_share()

    async def test_prepare_new_post_anchor_missing_fails(self, tmp_path):
        # If the New Post svg-anchor is absent AND no legacy Create candidate is
        # visible, opening the composer fails cleanly (no attach, no share).
        browser = FakeIGBrowser(
            authenticated=True, svg_anchors=set(),   # New Post / Post anchors absent
            composer_opens=False, file_input_present=0,  # composer never opens
        )
        # Keep only the auth signal visible so prepare passes the auth gate and
        # reaches the composer-open step (but the legacy Create fallback, if
        # clicked, still cannot open a composer here).
        browser._visible = set(C.LOC_AUTH_SIGNAL_CANDIDATES[:1])
        res = await HermesInstagramWorkflow().prepare(
            _instruction(media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.FAILED
        assert not any(c[0] == "set_input_files" for c in browser.calls)
        assert not browser.clicked_share()

    async def test_prepare_empty_caption_ok(self, tmp_path):
        browser = FakeIGBrowser(authenticated=True, attached=1)
        res = await HermesInstagramWorkflow().prepare(
            _instruction(caption="", media_paths=[_real_image(tmp_path)]), browser
        )
        assert res.status == ExternalResultStatus.ACTION_REQUIRED
        # No caption fill attempted (empty), but Share verified, not clicked.
        assert not browser.clicked_share()


# ══════════════════════════════════════════════════════════════════════════════
# CAPTION ENTRY (_fill_caption) — live-UI locator
# ══════════════════════════════════════════════════════════════════════════════

class TestCaptionEntry:
    """
    _fill_caption() must succeed against the CURRENT live Instagram caption
    editor, exposed as role="textbox" with accessible name "Add a caption..."
    (a contenteditable div). The older "Write a caption…" names remain as
    fallbacks.
    """

    async def test_fill_caption_succeeds_with_live_add_a_caption_locator(self):
        # Only the live locator is visible — the old "Write a caption…" names are
        # NOT present, proving the fix (not a stale fallback) is what matches.
        browser = FakeIGBrowser(authenticated=True, attached=1)
        browser._visible = {("textbox", "Add a caption...")}
        ok = await HermesInstagramWorkflow()._fill_caption(browser, "hello world")
        assert ok is True
        # It filled the exact live caption locator.
        assert ("fill", "textbox", "Add a caption...", "hello world") in browser.calls

    async def test_fill_caption_fails_when_no_caption_field_present(self):
        # No caption candidate visible at all → _fill_caption returns False and
        # types nothing (preserves the existing fail-safe behavior).
        browser = FakeIGBrowser(authenticated=True, attached=1)
        browser._visible = set()
        ok = await HermesInstagramWorkflow()._fill_caption(browser, "hello")
        assert ok is False
        assert not any(c[0] == "fill" for c in browser.calls)

    def test_live_locator_is_first_caption_candidate(self):
        # Guard: the confirmed live locator is present and ordered first.
        assert C.LOC_CAPTION_CANDIDATES[0] == ("textbox", "Add a caption...")
        assert ("textbox", "Write a caption...") in C.LOC_CAPTION_CANDIDATES


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE-ATTACH SIGNAL (_image_attached)
# ══════════════════════════════════════════════════════════════════════════════

class TestImageAttachedSignal:
    """
    _image_attached() must recognize a successful attach from ANY ONE of the
    signals the live composer produces, and must fail-safe when NONE is present.
    The mere presence of an input[type=file] is NOT a success signal.
    """

    async def test_blob_or_data_img_preview_is_attached(self):
        # Classic signal: a blob:/data: <img> crop preview is rendered.
        browser = FakeIGBrowser(authenticated=True, attached=1, bg_preview=0)
        browser._visible = browser._visible - set(C.LOC_NEXT_CANDIDATES)  # isolate signal 1
        assert await HermesInstagramWorkflow()._image_attached(browser) is True
        assert ("count_attached_files", C.IMAGE_PREVIEW_SELECTOR) in browser.calls

    async def test_blob_background_preview_is_attached(self):
        # Current composer: no <img> preview, but a blob-backed CSS background
        # preview is present (bg_preview=1). This must count as attached.
        browser = FakeIGBrowser(authenticated=True, attached=0, bg_preview=1)
        browser._visible = browser._visible - set(C.LOC_NEXT_CANDIDATES)  # isolate signal 2
        assert await HermesInstagramWorkflow()._image_attached(browser) is True
        assert ("count_attached_files", C.IMAGE_PREVIEW_BG_SELECTOR) in browser.calls

    async def test_visible_next_button_is_attached(self):
        # No preview of either kind, but the composer's "Next" button is visible
        # (Instagram only surfaces Next AFTER media attaches) → attached.
        browser = FakeIGBrowser(authenticated=True, attached=0, bg_preview=0)
        browser._visible = set(C.LOC_NEXT_CANDIDATES[:1])  # only Next visible
        assert await HermesInstagramWorkflow()._image_attached(browser) is True

    async def test_no_signal_is_not_attached(self):
        # No <img> preview, no blob background, no visible Next → NOT attached,
        # even though a file input would still be present on the page.
        browser = FakeIGBrowser(
            authenticated=True, attached=0, bg_preview=0, file_input_present=1,
        )
        browser._visible = set()  # nothing visible, incl. no Next
        assert await HermesInstagramWorkflow()._image_attached(browser) is False


# ══════════════════════════════════════════════════════════════════════════════
# CONFIRM / PUBLISH / VERIFY
# ══════════════════════════════════════════════════════════════════════════════

class TestConfirmPublish:
    async def test_confirm_published_via_permalink(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "SHARE_POLL_INTERVAL_S", 0)
        permalink = f"{C.IG_BASE}/p/ABC123/"
        browser = FakeIGBrowser(authenticated=True, attached=1, permalink=permalink)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        assert out["status"] == "action_required"
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_url"] == permalink
        assert res["external_post_id"] == "ABC123"
        assert browser.clicked_share()
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "published"

    async def test_prepare_closes_browser_and_does_not_cache(self, tmp_path, monkeypatch):
        # IMMEDIATE-PUBLISH MODEL: prepare opens its own browser, verifies the
        # post is ready, then CLOSES it. It does NOT keep the browser alive and
        # does NOT populate the live-session cache.
        from app.social_publishing.providers.hermes.live_session_cache import live_session_cache
        monkeypatch.setattr(C, "SHARE_POLL_INTERVAL_S", 0)
        browser = FakeIGBrowser(authenticated=True, attached=1)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        cid = out["confirmation_id"]
        # Prepare closed the browser and left nothing in the cache.
        assert browser.closed is True
        assert live_session_cache.get(cid) is None

    async def test_confirm_always_reprepares_then_publishes(self, tmp_path, monkeypatch):
        # IMMEDIATE-PUBLISH MODEL: confirm always opens a fresh browser and
        # re-prepares (a second attach happens), then publishes straight through.
        # Opening the browser again at publish time is expected.
        monkeypatch.setattr(C, "SHARE_POLL_INTERVAL_S", 0)
        permalink = f"{C.IG_BASE}/p/REUSE1/"
        browser = FakeIGBrowser(authenticated=True, attached=1, permalink=permalink)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        cid = out["confirmation_id"]
        attach_after_prepare = sum(1 for c in browser.calls if c[0] == "set_input_files")

        res = await svc.confirm("t1", cid)
        assert res["status"] == "published"
        assert browser.clicked_share()
        # Confirm re-prepared → a SECOND attach happened (fresh browser flow).
        attach_after_confirm = sum(1 for c in browser.calls if c[0] == "set_input_files")
        assert attach_after_confirm > attach_after_prepare

    async def test_cancel_marks_cancelled(self, tmp_path):
        # IMMEDIATE-PUBLISH MODEL: prepare keeps no live browser, so cancel just
        # marks the pending record cancelled — nothing to evict/close.
        browser = FakeIGBrowser(authenticated=True, attached=1)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        cid = out["confirmation_id"]
        res = await svc.cancel("t1", cid)
        assert res["status"] == "cancelled"
        row = await repo.find(cid, "t1")
        assert row.status == "cancelled"

    async def test_confirm_published_via_shared_confirmation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "SHARE_POLL_INTERVAL_S", 0)
        # No permalink in the bar, but the "shared" confirmation appears + profile
        # reconciliation returns exactly one recent permalink.
        browser = FakeIGBrowser(
            authenticated=True, attached=1, permalink=None, shared_confirmation=True,
            profile_permalinks=[f"{C.IG_BASE}/p/XYZ789/"],
        )
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "published"
        assert res["external_url"] == f"{C.IG_BASE}/p/XYZ789/"
        assert browser.clicked_share()

    async def test_confirm_unknown_when_no_signal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "SHARE_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "SHARE_POLL_MAX_CHECKS", 2)
        browser = FakeIGBrowser(
            authenticated=True, attached=1, permalink=None, shared_confirmation=False,
        )
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        res = await svc.confirm("t1", out["confirmation_id"])
        assert res["status"] == "unknown"
        assert browser.clicked_share()  # Share WAS clicked (post may exist)
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "unknown"

    async def test_verify_never_clicks_share(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "SHARE_POLL_INTERVAL_S", 0)
        monkeypatch.setattr(C, "SHARE_POLL_MAX_CHECKS", 2)
        browser = FakeIGBrowser(authenticated=True, attached=1, permalink=None)
        await HermesInstagramWorkflow().verify(_instruction(media_paths=[_real_image(tmp_path)]), browser)
        assert not browser.clicked_share()


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE: prepare/cancel/list + isolation + no secrets
# ══════════════════════════════════════════════════════════════════════════════

class TestService:
    async def test_prepare_persists_pending_instagram(self, tmp_path):
        browser = FakeIGBrowser(authenticated=True, attached=1)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        assert out["status"] == "action_required"
        assert out["action"] == C.CONFIRM_ACTION
        assert out["preview"]["media_count"] == 1
        assert out["preview"]["caption"] == "cap"
        row = await repo.find(out["confirmation_id"], "t1")
        assert row is not None and row.status == "action_required" and row.platform == "instagram"

    async def test_cancel_without_publish(self, tmp_path):
        browser = FakeIGBrowser(authenticated=True, attached=1)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        res = await svc.cancel("t1", out["confirmation_id"])
        assert res["status"] == "cancelled"
        row = await repo.find(out["confirmation_id"], "t1")
        assert row.status == "cancelled"

    async def test_list_pending_only_instagram_and_tenant_scoped(self, tmp_path):
        browser = FakeIGBrowser(authenticated=True, attached=1)
        svc, repo = _service(browser)
        await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        await svc.prepare("t2", "acc9", "op2", "cap", [_real_image(tmp_path)])
        # Inject a non-instagram pending row for t1 to prove platform filtering.
        await repo.create("t1", "acc1", "opR", "reddit", "hermes",
                          title="t", body="b", subreddit="test", media_paths=[])
        t1 = await svc.list_pending("t1")
        t2 = await svc.list_pending("t2")
        assert len(t1) == 1 and t1[0]["operation_id"] == "op1" and t1[0]["platform"] == "instagram"
        assert len(t2) == 1 and t2[0]["operation_id"] == "op2"

    async def test_confirm_cross_tenant_not_found(self, tmp_path):
        browser = FakeIGBrowser(authenticated=True, attached=1)
        svc, repo = _service(browser)
        out = await svc.prepare("t1", "acc1", "op1", "cap", [_real_image(tmp_path)])
        res = await svc.confirm("t2", out["confirmation_id"])
        assert res["status"] == "not_found"
        assert not browser.clicked_share()

    def test_profile_key_is_isolated_and_namespaced(self):
        svc = InstagramUserAssistedService(
            pending_repo=FakePendingRepo(), workflow=HermesInstagramWorkflow(),
            adapter_factory=lambda k: None,
            username_resolver=lambda a, t: _async_return(""),
        )
        k1 = svc._profile_key("t1", "acc1")
        k2 = svc._profile_key("t1", "acc2")
        k3 = svc._profile_key("t2", "acc1")
        assert k1 != k2 and k1 != k3        # per-account AND per-tenant isolation
        assert k1.startswith("ig_")         # namespaced away from Reddit profiles

    async def test_no_secret_fields_in_pending_model(self):
        import dataclasses
        from app.social_publishing.domain.models import PendingConfirmation
        fields = {f.name for f in dataclasses.fields(PendingConfirmation)}
        for forbidden in ("password", "cookie", "cookies", "session",
                          "access_token", "token", "credentials", "secret"):
            assert forbidden not in fields


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULED-JOB PATH + FEATURE FLAG
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduledPathAndFlag:
    async def test_execute_with_non_semantic_adapter_action_required(self):
        class Coarse:
            pass
        res = await HermesInstagramWorkflow().execute(_instruction(), Coarse())
        assert res.status == ExternalResultStatus.ACTION_REQUIRED

    def test_flag_defaults_false(self, monkeypatch):
        import app.config as c
        monkeypatch.delenv("ENABLE_HERMES_INSTAGRAM", raising=False)
        assert c._bool_env("ENABLE_HERMES_INSTAGRAM", False) is False

    def test_routes_404_when_flag_disabled(self, monkeypatch):
        import app.config as c
        from fastapi import HTTPException
        from app.social_publishing.api import instagram_routes
        monkeypatch.setattr(c, "ENABLE_HERMES_PROVIDER", True, raising=False)
        monkeypatch.setattr(c, "ENABLE_HERMES_INSTAGRAM", False, raising=False)
        with pytest.raises(HTTPException) as e:
            instagram_routes._require_flag()
        assert e.value.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# PERMALINK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

class TestPermalinkHelpers:
    def test_is_instagram_permalink(self):
        assert _is_instagram_permalink(f"{C.IG_BASE}/p/ABC/") is True
        assert _is_instagram_permalink("https://evil.com/p/ABC/") is False
        assert _is_instagram_permalink("/p/ABC/") is False           # relative
        assert _is_instagram_permalink(f"{C.IG_BASE}/tester/") is False  # not a post

    def test_permalink_id(self):
        assert _permalink_id(f"{C.IG_BASE}/p/ABC123/") == "ABC123"
        assert _permalink_id(f"{C.IG_BASE}/p/ABC123/?x=1") == "ABC123"
        assert _permalink_id("") is None
