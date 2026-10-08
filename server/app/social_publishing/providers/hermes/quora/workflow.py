"""HermesQuoraWorkflow — user-assisted Quora publishing (profile/space Post).

Mirrors HermesFacebookWorkflow / HermesInstagramWorkflow. USER-ASSISTED,
two-phase:

  Phase A — prepare():
    connect → verify authenticated session → navigate to Quora → open the
    create-post composer → type the text → (optionally) attach ONE image and
    verify it attached → confirm the Post button exists → STOP. Returns
    ACTION_REQUIRED. The Post button is NOT clicked.

  Phase B — confirm_and_publish():  (only after explicit user confirmation)
    reuse the SAME browser session (composer still prepared) → click Post →
    verify the post published → PUBLISHED. On uncertainty → UNKNOWN (never
    blind-retry, to avoid duplicate posts).

Talks ONLY to the SemanticBrowser interface — never imports Playwright, never
bypasses login/MFA/CAPTCHA. If Quora requires auth or human verification, the
workflow returns ACTION_REQUIRED and lets the user handle it.

`execute()` runs Phase A only and returns ACTION_REQUIRED, so a scheduled job
pauses for the user instead of publishing silently (same as the other Hermes
workflows).
"""

import asyncio
import time
from typing import Optional

from app.services.logger import log
from app.social_publishing.providers.capabilities import ProviderCapabilities
from app.social_publishing.providers.errors import (
    ExternalErrorCode,
    ExternalResultStatus,
    PublishInstruction,
    ProviderResult,
)
from app.social_publishing.providers.hermes.constants import (
    HERMES_ACTION_TIMEOUT,
    HERMES_NAVIGATION_TIMEOUT,
    HERMES_PUBLISH_TIMEOUT,
)
from app.social_publishing.providers.hermes.quora import constants as C
from app.social_publishing.providers.hermes.quora.validation import validate_post
from app.social_publishing.providers.hermes.semantic_browser import (
    BrowserError,
    BrowserTimeout,
    NavigationError,
    SemanticBrowser,
)
from app.social_publishing.providers.hermes.media_workspace import (
    MediaValidationError,
    validate_media,
)


class HermesQuoraWorkflow:
    """Quora user-assisted publishing workflow (implements HermesPlatformWorkflow)."""

    # ── HermesPlatformWorkflow contract ─────────────────────────────────────────

    @property
    def platform(self) -> str:
        return C.PLATFORM

    @property
    def automation_allowed(self) -> bool:
        return True

    @property
    def requires_user_action(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        # This phase: text and/or a single IMAGE. No video/carousel.
        return ProviderCapabilities(
            text=True,
            image=True,
            video=C.SUPPORTS_VIDEO,   # False
            links=True,
            scheduling=False,
            analytics=False,
            browser_automation=True,
            user_confirmation=True,
        )

    async def execute(
        self, instruction: PublishInstruction, adapter: "object"
    ) -> ProviderResult:
        """
        Protocol entry point. Runs Phase A (prepare) and returns ACTION_REQUIRED.

        On the scheduled-job path there is no live semantic browser, so we signal
        ACTION_REQUIRED — Quora is user-assisted and never auto-publishes in the
        background (same guarantee as the other Hermes workflows).
        """
        browser = _as_semantic_browser(adapter)
        if browser is None:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.ACTION_REQUIRED,
                error_code=ExternalErrorCode.ACTION_REQUIRED,
                error_message="Quora posting requires your confirmation in the browser.",
            )
        return await self.prepare(instruction, browser)

    # ── Phase A: prepare (stops at ACTION_REQUIRED) ─────────────────────────────

    async def prepare(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Prepare the Quora post up to (but NOT including) the final Post click.

        Returns ACTION_REQUIRED on success. Any authentication requirement also
        returns ACTION_REQUIRED (the user completes login/MFA in the browser).
        Validation failures return FAILED (no browser action taken).
        """
        post = _post_from_instruction(instruction)
        parsed, errors = validate_post(post["text"], post.get("media_paths", []))
        if errors:
            return _failed(f"Invalid Quora post: {'; '.join(errors)}",
                           ExternalErrorCode.EXTERNAL_CONTENT_REJECTED, retryable=False)

        # Validate the actual image file(s) on disk BEFORE opening the browser.
        media_error = _validate_image_files(parsed.media_paths)
        if media_error:
            return _failed(f"Invalid Quora image: {media_error}",
                           ExternalErrorCode.EXTERNAL_CONTENT_REJECTED, retryable=False)

        try:
            # 1. Navigate to Quora and verify an authenticated session.
            if not await self._goto_authenticated(browser):
                await browser.goto(C.LOGIN_URL, timeout_s=HERMES_NAVIGATION_TIMEOUT)
                return ProviderResult(
                    success=False,
                    status=ExternalResultStatus.ACTION_REQUIRED,
                    error_code=ExternalErrorCode.ACTION_REQUIRED,
                    error_message="Quora login required — complete sign-in (and MFA) in the browser",
                )

            # 2. Open the create-post composer.
            if not await self._open_composer(browser):
                return _failed("Could not open the Quora composer", retryable=True)

            # 3. Type the post text (optional only when an image is present).
            if parsed.text.strip():
                if not await self._fill_text(browser, parsed.text):
                    return _failed("Could not enter the Quora post text", retryable=True)

            # 4. Optionally attach ONE image and verify it attached.
            log.warning(f"QUORA_PREPARE media_paths={len(parsed.media_paths)} "
                        f"first={(parsed.media_paths[0] if parsed.media_paths else None)!r}")
            if parsed.media_paths:
                attached = await self._attach_image(browser, parsed.media_paths)
                log.warning(f"QUORA_PREPARE image_attached={attached}")
                if not attached:
                    return _failed("Could not attach the image to the Quora post", retryable=True)

            # 5. Confirm the Post button exists (prepared) — but DO NOT click it.
            if not await self._first_visible(browser, C.LOC_POST_BUTTON_CANDIDATES, timeout_s=HERMES_ACTION_TIMEOUT):
                return _failed("Quora Post control not found", retryable=True)

            # 6. STOP. Hand control back to the user for explicit confirmation.
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.ACTION_REQUIRED,
                error_code=ExternalErrorCode.ACTION_REQUIRED,
                error_message="Your Quora post is ready. Confirm publishing.",
            )

        except (BrowserTimeout, NavigationError, BrowserError) as e:
            log.warning("Quora prepare failed", op=instruction.operation_id, err=type(e).__name__)
            return _failed("Could not prepare the Quora post", retryable=True)

    # ── Phase B: confirm + publish (after explicit user confirmation) ───────────

    async def confirm_and_publish(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Resume the SAME session (composer still present) and click Post.

        The Post button is clicked here — only reached after the user explicitly
        confirmed. Then verify the publish. On any uncertainty after the click,
        return UNKNOWN (never blind-retry).

        TIMING: emits [QUORA_TIMING] stage=<elapsed>ms logs (read-only; no sleeps,
        no behavior change) so a real publish can be profiled stage-by-stage.
        """
        # t0 for all QUORA_TIMING logs in confirm. The service may set
        # _timing_t0 earlier (to capture cache-retrieve / composer_ready); if it
        # didn't, start the clock here so confirm_and_publish is self-contained.
        if getattr(self, "_timing_t0", None) is None:
            self._timing_t0 = time.monotonic()
        self._tlog("confirm_publish_start")

        clicked = await self._click_post_button(browser)
        self._tlog("post_clicked" if clicked else "post_click_failed")
        if not clicked:
            # The Post control was not found/clicked → nothing was submitted.
            return _failed("Could not submit the Quora post", retryable=True)

        self._tlog("verification_started")
        result = await self._verify(instruction, browser)
        self._tlog(f"confirm_total status={result.status.value if hasattr(result.status, 'value') else result.status}")
        self._timing_t0 = None  # reset for the next confirm
        return result

    def _tlog(self, stage: str) -> None:
        """Emit a [QUORA_TIMING] stage=<elapsed>ms line relative to _timing_t0.

        Pure logging — never sleeps, never changes control flow. No-op if no
        timing baseline is set.
        """
        t0 = getattr(self, "_timing_t0", None)
        if t0 is None:
            return
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.warning(f"[QUORA_TIMING] {stage}={elapsed_ms}ms")

    async def composer_ready(self, browser: SemanticBrowser) -> bool:
        """
        True if a REUSED (kept-alive) composer is still prepared and safe to Post
        directly — the Post control is still visible. Lets the service skip the
        re-prepare on confirm. Never raises.
        """
        try:
            return await self._first_visible(
                browser, C.LOC_POST_BUTTON_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
            )
        except Exception:
            return False

    async def _click_post_button(self, browser: SemanticBrowser) -> bool:
        """
        Click the composer's final Post button.

        Strategy (ordered): a DIALOG-SCOPED CSS click first — Quora's Post is
        typically an aria-labelled role=button, and click_css skips aria-disabled
        placeholders and targets the real enabled control inside the dialog —
        then the accessible (role, name) candidates as a fallback. This only
        clicks Post; it never touches prepare/navigation/text.
        """
        click_css = getattr(browser, "click_css", None)
        if callable(click_css):
            for selector in C.POST_BUTTON_CSS_CANDIDATES:
                try:
                    if await click_css(selector, timeout_s=HERMES_ACTION_TIMEOUT):
                        self._tlog(f"post_button_clicked_via_css={selector[:40]}")
                        return True
                except Exception:
                    continue
        self._tlog("post_button_css_miss_fallback_role_name")
        return await self._first_visible(
            browser, C.LOC_POST_BUTTON_CANDIDATES, timeout_s=HERMES_ACTION_TIMEOUT, click=True
        )

    async def verify(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """Public verify entry (used for UNKNOWN → VERIFYING re-checks)."""
        return await self._verify(instruction, browser)

    async def _verify(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Verify the post published.

        Signal order (strongest deterministic first):
          1. The address bar becomes a post permalink.
          2. A success toast appears.
          3. The composer dialog closed after the Post click — the RELIABLE
             signal for Quora posts, which do not reliably expose a permalink.
        Absence of a clear signal → UNKNOWN (never blind-retried). Never clicks
        Post.
        """
        try:
            outcome = await self._await_post_result(browser)
            self._tlog(f"verification_resolved outcome={outcome}")
            if outcome == "permalink":
                url = await _safe_current_url(browser)
                if url and _is_quora_permalink(url):
                    return ProviderResult(
                        success=True,
                        status=ExternalResultStatus.PUBLISHED,
                        external_post_id=_permalink_id(url),
                        external_url=url,
                    )
            if outcome in ("confirmed", "dialog_closed"):
                # Published. A permalink is rarely in the address bar for Quora
                # posts, so external_url may be None — that is OK; the post is
                # live. (The composer closing after Post is the reliable signal.)
                url = await _safe_current_url(browser)
                has_perma = bool(url and _is_quora_permalink(url))
                return ProviderResult(
                    success=True,
                    status=ExternalResultStatus.PUBLISHED,
                    external_post_id=_permalink_id(url) if has_perma else None,
                    external_url=url if has_perma else None,
                )
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.UNKNOWN,
                error_code=ExternalErrorCode.EXTERNAL_PUBLISH_UNKNOWN,
                error_message="Quora publish result is uncertain — verification required",
                retryable=False,
            )
        except BrowserError:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_VERIFICATION_FAILED,
                error_message="Could not verify the Quora post",
                retryable=False,
            )

    # ── Internal helpers ────────────────────────────────────────────────────────

    async def _goto_authenticated(self, browser: SemanticBrowser) -> bool:
        """
        Navigate to Quora home and detect an authenticated session.

        Returns True only when logged in (a composer-entry control is present AND
        no login form is showing). No credentials are typed here.
        """
        try:
            await browser.goto(C.HOME_URL, timeout_s=HERMES_NAVIGATION_TIMEOUT)
        except BrowserError:
            return False
        login_visible = await self._first_visible(
            browser, C.LOC_LOGIN_SIGNAL_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        )
        if login_visible:
            return False
        return await self._first_visible(
            browser, C.LOC_AUTH_SIGNAL_CANDIDATES, timeout_s=HERMES_ACTION_TIMEOUT
        )

    async def _open_composer(self, browser: SemanticBrowser) -> bool:
        """
        Open a FRESH create-post composer and verify it opened.

        CLEAN SLATE FIRST: a leftover/draft composer from a previous session (or
        a diagnostic run) could otherwise carry stale text/image into the new
        post. So before opening, we discard any existing composer and re-navigate
        to a clean home page, guaranteeing the composer we open contains only the
        current content. Verified by the create-post dialog / text editor
        becoming visible.
        """
        await self._discard_stale_composer(browser)
        for _ in range(3):
            if await self._first_visible(
                browser, C.LOC_OPEN_COMPOSER_CANDIDATES, timeout_s=C.PROBE_TIMEOUT, click=True
            ):
                if await self._composer_open(browser):
                    return True
            await asyncio.sleep(C.POST_POLL_INTERVAL_S)
        return await self._composer_open(browser)

    async def _discard_stale_composer(self, browser: SemanticBrowser) -> None:
        """
        Ensure no leftover composer/draft is present before composing a new post.

        If a composer dialog is already open, try its close/discard controls;
        then re-navigate to the home URL, which abandons any in-page composer and
        guarantees a clean slate. Best-effort and bounded — never raises; if the
        page is already clean this is a cheap no-op beyond one navigation.
        """
        try:
            composer_present = await browser.is_css_visible(
                C.COMPOSER_DIALOG_SELECTOR, timeout_s=C.PROBE_TIMEOUT
            )
        except Exception:
            composer_present = False

        if composer_present:
            # Try explicit close/discard controls (best-effort).
            await self._first_visible(
                browser, C.LOC_COMPOSER_CLOSE_CANDIDATES, timeout_s=C.PROBE_TIMEOUT, click=True
            )
            await self._first_visible(
                browser, C.LOC_DISCARD_CONFIRM_CANDIDATES, timeout_s=C.PROBE_TIMEOUT, click=True
            )

        # Re-navigate to a clean home page — abandons any lingering composer /
        # draft state so the next composer opens empty.
        try:
            await browser.goto(C.HOME_URL, timeout_s=HERMES_NAVIGATION_TIMEOUT)
        except BrowserError:
            pass

    async def _composer_open(self, browser: SemanticBrowser) -> bool:
        """
        True if the create-post composer is open, using robust indicators (any
        one is sufficient):
          - a role=dialog is visible, OR
          - a "Create Post" heading is visible, OR
          - the main text editor is visible.
        """
        try:
            if await browser.is_css_visible(C.COMPOSER_DIALOG_SELECTOR, timeout_s=C.PROBE_TIMEOUT):
                return True
        except Exception:
            pass
        if await self._first_visible(
            browser, C.LOC_COMPOSER_DIALOG_TITLE_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        ):
            return True
        return await self._first_visible(
            browser, C.LOC_TEXT_EDITOR_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        )

    async def _fill_text(self, browser: SemanticBrowser, text: str) -> bool:
        """
        Type the post text into the composer's text editor.

        CLEARS the editor FIRST: Quora persists the composer text as a draft that
        can be restored into a fresh composer, and a plain fill may append rather
        than replace. We select-all + delete the editor before typing so the post
        contains ONLY the requested text (no leftover draft). The live editor is a
        contenteditable DIV whose accessible name may be exposed only via a
        placeholder, so we target it with a DIALOG-SCOPED CSS selector first
        (fill_css), then fall back to the accessible (role, name) candidates.
        """
        clear_css = getattr(browser, "clear_css", None)
        if callable(clear_css):
            for selector in C.TEXT_EDITOR_CSS_CANDIDATES:
                try:
                    if await clear_css(selector, timeout_s=C.PROBE_TIMEOUT):
                        break
                except Exception:
                    continue
        fill_css = getattr(browser, "fill_css", None)
        if callable(fill_css):
            for selector in C.TEXT_EDITOR_CSS_CANDIDATES:
                try:
                    if await fill_css(selector, text, timeout_s=HERMES_ACTION_TIMEOUT):
                        return True
                except Exception:
                    continue
        for role, name in C.LOC_TEXT_EDITOR_CANDIDATES:
            try:
                if await browser.is_visible(role, name, timeout_s=C.PROBE_TIMEOUT):
                    await browser.fill(role, name, text, timeout_s=HERMES_ACTION_TIMEOUT)
                    return True
            except BrowserError:
                continue
        return False

    async def _attach_image(self, browser: SemanticBrowser, media_paths: list[str]) -> bool:
        """
        Attach ONE image by writing the file DIRECTLY to the composer's hidden
        <input type=file> and verify a preview rendered.

        We NEVER click the "Add image" control — clicking it opens the browser's
        native OS file picker, which blocks automation. set_input_files writes
        the file straight into the hidden input, exactly like the Facebook and
        Instagram workflows. Single image only (first path).
        """
        selector = await self._mounted_file_input_selector(browser)
        if selector is None:
            return False
        try:
            await browser.set_input_files(
                selector, media_paths[:1], timeout_s=HERMES_ACTION_TIMEOUT
            )
        except BrowserError:
            return False
        return await self._image_attached(browser)

    async def _mounted_file_input_selector(self, browser: SemanticBrowser):
        """
        Poll (bounded) for a composer file input and return the selector that
        matched — preferring the dialog-scoped input, falling back to the generic
        one. Read-only (count only); never clicks. Returns None if none mounts.
        """
        selectors = (C.IMAGE_FILE_INPUT_SELECTOR, C.IMAGE_FILE_INPUT_SELECTOR_GENERIC)
        for _ in range(C.POST_POLL_MAX_CHECKS):
            for sel in selectors:
                try:
                    if await browser.count_attached_files(sel, timeout_s=C.PROBE_TIMEOUT) >= 1:
                        return sel
                except Exception:
                    continue
            await asyncio.sleep(C.POST_POLL_INTERVAL_S)
        for sel in selectors:
            try:
                if await browser.count_attached_files(sel, timeout_s=C.PROBE_TIMEOUT) >= 1:
                    return sel
            except Exception:
                continue
        return None

    async def _image_attached(self, browser: SemanticBrowser) -> bool:
        """
        Confirm the image attached, using any ONE of the signals the composer
        produces (bounded, read-only — never clicks Post):
          1. A sizeable Quora-CDN preview <img> inside the composer dialog — the
             CONFIRMED Quora signal: Quora uploads the image to its CDN on attach
             and renders a large quoracdn.net <img>. We require a minimum rendered
             size so the small (~40px) avatar is not mistaken for the preview.
          2. A blob:/data: preview <img> (fallback for UI variants).
          3. A blob-backed CSS background preview (fallback).

        The mere presence of input[type=file] is NOT success. Quora uploads take
        a moment, so we poll briefly.
        """
        count_large = getattr(browser, "count_large_images", None)
        for _ in range(C.POST_POLL_MAX_CHECKS):
            # 1. Quora CDN preview with a minimum size (excludes the avatar).
            if callable(count_large):
                try:
                    if await count_large(
                        C.IMAGE_PREVIEW_CDN_SELECTOR, C.IMAGE_PREVIEW_MIN_SIZE,
                        timeout_s=C.PROBE_TIMEOUT,
                    ) >= 1:
                        return True
                except Exception:
                    pass
            # 2. blob:/data: <img> preview.
            try:
                if await browser.count_attached_files(
                    C.IMAGE_PREVIEW_SELECTOR, timeout_s=C.PROBE_TIMEOUT
                ) >= 1:
                    return True
            except Exception:
                pass
            # 3. blob-backed CSS background preview.
            try:
                if await browser.count_attached_files(
                    C.IMAGE_PREVIEW_BG_SELECTOR, timeout_s=C.PROBE_TIMEOUT
                ) >= 1:
                    return True
            except Exception:
                pass
            await asyncio.sleep(C.POST_POLL_INTERVAL_S)
        return False

    async def _first_visible(
        self, browser: SemanticBrowser, candidates, *, timeout_s: float, click: bool = False
    ) -> bool:
        """
        Return True if any (role, name) candidate is visible; optionally click the
        first visible one. Never raises — used for best-effort locator probing.
        """
        for role, name in candidates:
            try:
                if await browser.is_visible(role, name, timeout_s=timeout_s):
                    if click:
                        await browser.click(role, name, timeout_s=HERMES_ACTION_TIMEOUT)
                    return True
            except BrowserError:
                continue
        return False

    async def _submit_control_present(self, browser: SemanticBrowser) -> bool:
        """
        FAST, SPECIFIC "is the composer still open?" probe used ONLY during
        verification. Checks a SINGLE thing — the composer's own Post button —
        with ONE short timeout, instead of running the full multi-candidate
        composer-open cascade (dialog + title + text-editor), each at
        PROBE_TIMEOUT, on every poll iteration.

        WHY: `_composer_open` matches a bare `div[role='dialog']` and a list of
        editor/title candidates. After a successful publish Quora can leave an
        unrelated dialog/overlay node mounted, so `_composer_open` kept returning
        True and verification never saw the composer "close" → UNKNOWN. It was
        also slow: when the composer HAS closed, every candidate in the cascade
        must time out before it returns False (~full cascade × PROBE_TIMEOUT per
        attempt). The composer's submit button (button.puppeteer_test_modal_submit)
        reliably DISAPPEARS when the post is accepted, so its absence is the
        correct, fast success signal. Read-only; never clicks; never raises.
        """
        is_css = getattr(browser, "is_css_visible", None)
        if callable(is_css):
            for selector in C.POST_BUTTON_CSS_CANDIDATES:
                try:
                    if await is_css(selector, timeout_s=C.PROBE_TIMEOUT):
                        return True
                except Exception:
                    continue
            return False
        # No CSS probe available → fall back to the accessible Post candidates.
        return await self._first_visible(
            browser, C.LOC_POST_BUTTON_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        )

    async def _await_post_result(self, browser: SemanticBrowser) -> Optional[str]:
        """
        Poll for a post outcome, bounded. Returns one of:
          - 'permalink'     : URL became a post permalink (strongest; gives URL)
          - 'confirmed'     : a success toast appeared
          - 'dialog_closed' : the composer's Post button disappeared after the
                              Post click — the RELIABLE success signal for Quora
          - None            : none of the above within the bound (uncertain)

        Each attempt probes ONLY fast, specific signals (permalink, one success
        toast check, and the single-selector submit-control-gone check) so an
        attempt costs ~1-2s instead of the former full-cascade ~18s.
        """
        deadline = time.monotonic() + HERMES_PUBLISH_TIMEOUT
        checks = 0
        while checks < C.POST_POLL_MAX_CHECKS and time.monotonic() < deadline:
            checks += 1
            url = await _safe_current_url(browser)
            if url and _is_quora_permalink(url):
                self._tlog(f"verification_attempt_{checks} outcome=permalink")
                return "permalink"
            if await self._first_visible(
                browser, C.POST_SUCCESS_TEXT_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
            ):
                self._tlog(f"verification_attempt_{checks} outcome=confirmed")
                return "confirmed"
            if not await self._submit_control_present(browser):
                self._tlog(f"verification_attempt_{checks} outcome=dialog_closed")
                return "dialog_closed"
            self._tlog(f"verification_attempt_{checks} outcome=pending")
            await asyncio.sleep(C.POST_POLL_INTERVAL_S)
        url = await _safe_current_url(browser)
        if url and _is_quora_permalink(url):
            self._tlog("verification_final outcome=permalink")
            return "permalink"
        if not await self._submit_control_present(browser):
            self._tlog("verification_final outcome=dialog_closed")
            return "dialog_closed"
        self._tlog("verification_final outcome=none")
        return None


# ── Module helpers ────────────────────────────────────────────────────────────

def _as_semantic_browser(adapter: object) -> Optional[SemanticBrowser]:
    return adapter if isinstance(adapter, SemanticBrowser) else None


def _post_from_instruction(instruction: PublishInstruction) -> dict:
    meta = instruction.meta or {}
    return {
        "text": instruction.text or "",
        "media_paths": list(meta.get("quora_media_paths", []) or []),
        "username": meta.get("quora_username", "") or "",
    }


def _failed(message: str, code: ExternalErrorCode = ExternalErrorCode.EXTERNAL_PROVIDER_UNAVAILABLE,
            *, retryable: bool) -> ProviderResult:
    return ProviderResult(
        success=False,
        status=ExternalResultStatus.FAILED,
        error_code=code,
        error_message=message,
        retryable=retryable,
    )


async def _safe_current_url(browser: SemanticBrowser) -> str:
    try:
        return await browser.current_url()
    except BrowserError:
        return ""


def _is_quora_permalink(url: str) -> bool:
    """True only for an absolute quora.com URL that looks like a post permalink."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    if not any(host == h or host.endswith("." + h) for h in C.QUORA_HOSTS):
        return False
    full = (parsed.path or "") + "?" + (parsed.query or "")
    return any(marker in full for marker in C.PERMALINK_MARKERS)


def _permalink_id(url: str) -> Optional[str]:
    """Best-effort: return the last non-empty path segment of a Quora permalink."""
    if not url:
        return None
    path = url.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in path.split("/") if p]
    return parts[-1] if parts else None


def _validate_image_files(media_paths: list[str]) -> Optional[str]:
    """
    Validate image files on disk before any browser action (existence + type +
    size), reusing the restricted media-workspace validator. Returns an error
    string on the first problem (basename only — no full path leak), else None.
    """
    import os

    for path in media_paths:
        name = os.path.basename(path) or "image"
        if not os.path.exists(path):
            return f"file not found ({name})"
        if not os.path.isfile(path):
            return f"not a file ({name})"
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        try:
            size = os.path.getsize(path)
            validate_media(ext, size)
        except MediaValidationError as e:
            return str(e)
        except OSError:
            return f"could not read file ({name})"
    return None
