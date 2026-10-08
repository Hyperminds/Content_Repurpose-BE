"""HermesFacebookWorkflow — user-assisted Facebook publishing (profile OR page).

ONE workflow serves BOTH targets. It is TARGET-AWARE: the destination URL and
the composer-open locators are selected from facebook.constants by the
target_type carried on the instruction (resolved from the connected account).
There are NO separate classes for profile vs page.

Mirrors HermesInstagramWorkflow / HermesRedditWorkflow. USER-ASSISTED, two-phase:

  Phase A — prepare():
    connect → verify authenticated session → navigate to the target (profile
    timeline or the configured Page) → open the create-post composer → type the
    text → (optionally) attach ONE image and verify it attached → confirm the
    Post button exists → STOP. Returns ACTION_REQUIRED. The Post button is NOT
    clicked.

  Phase B — confirm_and_publish():  (only after explicit user confirmation)
    reuse the SAME browser session (composer still prepared) → click Post →
    verify the post published → PUBLISHED. On uncertainty → UNKNOWN (never
    blind-retry, to avoid duplicate posts).

Talks ONLY to the SemanticBrowser interface — never imports Playwright, never
bypasses login/MFA/CAPTCHA. If Facebook requires auth or human verification, the
workflow returns ACTION_REQUIRED and lets the user handle it.

`execute()` runs Phase A only and returns ACTION_REQUIRED, so a scheduled job
pauses for the user instead of publishing silently (same as Instagram/Reddit).
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
from app.social_publishing.providers.hermes.facebook import constants as C
from app.social_publishing.providers.hermes.facebook.validation import validate_post
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


class HermesFacebookWorkflow:
    """Facebook user-assisted publishing workflow (implements HermesPlatformWorkflow)."""

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
        # This phase: text and/or a single IMAGE. No video/reels/carousel.
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
        ACTION_REQUIRED — Facebook is user-assisted and never auto-publishes in
        the background (same guarantee as Instagram/Reddit).
        """
        browser = _as_semantic_browser(adapter)
        if browser is None:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.ACTION_REQUIRED,
                error_code=ExternalErrorCode.ACTION_REQUIRED,
                error_message="Facebook posting requires your confirmation in the browser.",
            )
        return await self.prepare(instruction, browser)

    # ── Phase A: prepare (stops at ACTION_REQUIRED) ─────────────────────────────

    async def prepare(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Prepare the Facebook post up to (but NOT including) the final Post click.

        Returns ACTION_REQUIRED on success. Any authentication requirement also
        returns ACTION_REQUIRED (the user completes login/MFA in the browser).
        Validation failures return FAILED (no browser action taken).
        """
        post = _post_from_instruction(instruction)
        parsed, errors = validate_post(
            post["text"], post.get("media_paths", []), post.get("target_type", C.DEFAULT_TARGET_TYPE)
        )
        if errors:
            return _failed(f"Invalid Facebook post: {'; '.join(errors)}",
                           ExternalErrorCode.EXTERNAL_CONTENT_REJECTED, retryable=False)

        # Validate the actual image file(s) on disk BEFORE opening the browser.
        media_error = _validate_image_files(parsed.media_paths)
        if media_error:
            return _failed(f"Invalid Facebook image: {media_error}",
                           ExternalErrorCode.EXTERNAL_CONTENT_REJECTED, retryable=False)

        target_type = parsed.target_type
        target_identifier = post.get("target_identifier", "")

        try:
            # 1. Navigate to the target (profile timeline or the Page) and verify
            #    an authenticated session.
            if not await self._goto_target_authenticated(browser, target_type, target_identifier):
                await browser.goto(C.LOGIN_URL, timeout_s=HERMES_NAVIGATION_TIMEOUT)
                return ProviderResult(
                    success=False,
                    status=ExternalResultStatus.ACTION_REQUIRED,
                    error_code=ExternalErrorCode.ACTION_REQUIRED,
                    error_message="Facebook login required — complete sign-in (and MFA) in the browser",
                )

            # 2. Open the create-post composer for this target.
            if not await self._open_composer(browser, target_type):
                return _failed("Could not open the Facebook composer", retryable=True)

            # 3. Type the post text (optional only when an image is present).
            if parsed.text.strip():
                if not await self._fill_text(browser, parsed.text):
                    return _failed("Could not enter the Facebook post text", retryable=True)

            # 4. Optionally attach ONE image and verify it attached.
            if parsed.media_paths:
                if not await self._attach_image(browser, parsed.media_paths):
                    return _failed("Could not attach the image to the Facebook post", retryable=True)

            # 5. Confirm the Post button exists (prepared) — but DO NOT click it.
            if not await self._first_visible(browser, C.LOC_POST_BUTTON_CANDIDATES, timeout_s=HERMES_ACTION_TIMEOUT):
                return _failed("Facebook Post control not found", retryable=True)

            # 6. STOP. Hand control back to the user for explicit confirmation.
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.ACTION_REQUIRED,
                error_code=ExternalErrorCode.ACTION_REQUIRED,
                error_message="Your Facebook post is ready. Confirm publishing.",
            )

        except (BrowserTimeout, NavigationError, BrowserError) as e:
            log.warning("Facebook prepare failed", op=instruction.operation_id, err=type(e).__name__)
            return _failed("Could not prepare the Facebook post", retryable=True)

    # ── Phase B: confirm + publish (after explicit user confirmation) ───────────

    async def confirm_and_publish(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Resume the SAME session (composer still present) and click Post.

        The Post button is clicked here — only reached after the user explicitly
        confirmed. Then verify the publish. On any uncertainty after the click,
        return UNKNOWN (never blind-retry).
        """
        clicked = await self._click_post_button(browser)
        if not clicked:
            # The Post control was not found/clicked → nothing was submitted.
            return _failed("Could not submit the Facebook post", retryable=True)

        return await self._verify(instruction, browser)

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

        Strategy (ordered): a DIALOG-SCOPED CSS click first — Facebook's Post is
        an aria-labelled role=button DIV, and click_css skips aria-disabled
        placeholders and targets the real enabled control inside the dialog —
        then the accessible (role, name) candidates as a fallback. This only
        clicks Post; it never touches prepare/navigation/text.
        """
        click_css = getattr(browser, "click_css", None)
        if callable(click_css):
            for selector in C.POST_BUTTON_CSS_CANDIDATES:
                try:
                    if await click_css(selector, timeout_s=HERMES_ACTION_TIMEOUT):
                        return True
                except Exception:
                    continue
        # Fallback: accessible-name candidates.
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
          1. The address bar becomes a post permalink (/posts/, /permalink/,
             story_fbid=).
          2. A "post is now published" confirmation appears.
          3. The composer dialog closed (weak signal) — treated as published only
             alongside one of the above; otherwise UNKNOWN.
        Absence of a clear signal → UNKNOWN (never blind-retried). Never clicks
        Post.
        """
        try:
            outcome = await self._await_post_result(browser)
            if outcome == "permalink":
                url = await _safe_current_url(browser)
                if url and _is_facebook_permalink(url):
                    return ProviderResult(
                        success=True,
                        status=ExternalResultStatus.PUBLISHED,
                        external_post_id=_permalink_id(url),
                        external_url=url,
                    )
            if outcome in ("confirmed", "dialog_closed"):
                # Published. A permalink is rarely in the address bar for
                # profile/page posts, so external_url may be None — that is OK;
                # the post is live. (The composer closing after Post is the
                # reliable Facebook success signal.)
                url = await _safe_current_url(browser)
                has_perma = bool(url and _is_facebook_permalink(url))
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
                error_message="Facebook publish result is uncertain — verification required",
                retryable=False,
            )
        except BrowserError:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_VERIFICATION_FAILED,
                error_message="Could not verify the Facebook post",
                retryable=False,
            )

    # ── Internal helpers ────────────────────────────────────────────────────────

    async def _goto_target_authenticated(
        self, browser: SemanticBrowser, target_type: str, target_identifier: str
    ) -> bool:
        """
        Navigate to the resolved target URL and detect an authenticated session.

        TARGET-AWARE: profile → /me, page → the configured Page URL. Returns True
        only when logged in (a composer-entry control is present AND no login
        form is showing). No credentials are typed here.
        """
        url = C.target_url(target_type, target_identifier)
        try:
            await browser.goto(url, timeout_s=HERMES_NAVIGATION_TIMEOUT)
        except BrowserError:
            return False
        # Login form visible → NOT authenticated.
        login_visible = await self._first_visible(
            browser, C.LOC_LOGIN_SIGNAL_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        )
        if login_visible:
            return False
        # Authenticated when a composer-entry control for THIS target is present.
        return await self._first_visible(
            browser, C.LOC_OPEN_COMPOSER_CANDIDATES.get(target_type, []), timeout_s=HERMES_ACTION_TIMEOUT
        )

    async def _open_composer(self, browser: SemanticBrowser, target_type: str) -> bool:
        """
        Open the create-post composer for the target and verify it opened.

        TARGET-AWARE: uses the per-target opener candidates. Verified by the
        create-post dialog / text editor becoming visible.
        """
        candidates = C.LOC_OPEN_COMPOSER_CANDIDATES.get(target_type, [])
        for _ in range(3):
            if await self._first_visible(browser, candidates, timeout_s=C.PROBE_TIMEOUT, click=True):
                if await self._composer_open(browser):
                    return True
            await asyncio.sleep(C.POST_POLL_INTERVAL_S)
        return await self._composer_open(browser)

    async def _composer_open(self, browser: SemanticBrowser) -> bool:
        """
        True if the create-post composer is open, using robust indicators (any
        one is sufficient):
          - a role=dialog is visible, OR
          - the "Create post" heading text is visible, OR
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

        The live editor is a contenteditable DIV whose only accessible hint is
        aria-placeholder, so we target it with a DIALOG-SCOPED CSS selector
        first (fill_css) — avoiding the feed's "Write a comment…" decoys — then
        fall back to the accessible (role, name) candidates.
        """
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

        IMPORTANT: we NEVER click the "Photo/video" control — clicking it opens
        the browser's native OS file picker, which blocks automation and is why
        the attach appeared to "open files" without posting. set_input_files
        writes the file straight into the hidden input, exactly like the Reddit
        and Instagram workflows. Single image only (first path).

        The composer mounts its file input lazily, so we poll for it, then set
        files via a dialog-scoped selector (falling back to the generic one).
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
        matched — preferring the Create-post-dialog-scoped input, falling back
        to the generic one. Read-only (count only); never clicks. Returns None
        if no file input mounts in time.
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
        # Final check.
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
          1. A blob:/data: preview <img>.
          2. A blob-backed CSS background preview.
        The mere presence of input[type=file] is NOT success.
        """
        try:
            if await browser.count_attached_files(
                C.IMAGE_PREVIEW_SELECTOR, timeout_s=HERMES_ACTION_TIMEOUT
            ) >= 1:
                return True
        except Exception:
            pass
        try:
            if await browser.count_attached_files(
                C.IMAGE_PREVIEW_BG_SELECTOR, timeout_s=C.PROBE_TIMEOUT
            ) >= 1:
                return True
        except Exception:
            pass
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

        WHY: `_composer_open` matches a bare `div[role='dialog']` plus editor/
        title candidates. After a successful publish Facebook can leave an
        unrelated dialog mounted, so `_composer_open` could keep returning True
        and verification never saw the composer "close" → UNKNOWN. It was also
        slow: when the composer HAS closed, every candidate in the cascade must
        time out before it returns False (~full cascade × PROBE_TIMEOUT per
        attempt). The dialog-scoped Post button reliably DISAPPEARS when the post
        is accepted, so its absence is the correct, fast success signal.
        Read-only; never clicks; never raises.
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
        return await self._first_visible(
            browser, C.LOC_POST_BUTTON_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        )

    async def _await_post_result(self, browser: SemanticBrowser) -> Optional[str]:
        """
        Poll for a post outcome, bounded. Returns one of:
          - 'permalink'  : URL became a post permalink (strongest; gives a URL)
          - 'confirmed'  : a success toast appeared
          - 'dialog_closed': the composer's Post button disappeared after the
                             Post click — the RELIABLE success signal for
                             Facebook profile/page posts, which do NOT navigate
                             to a permalink or always show a toast.
          - None         : none of the above within the bound (uncertain)

        Each attempt probes ONLY fast, specific signals (permalink, one success
        toast check, and the single-selector submit-control-gone check) so an
        attempt costs ~1-2s instead of the former full-cascade ~18s.
        """
        deadline = time.monotonic() + HERMES_PUBLISH_TIMEOUT
        checks = 0
        while checks < C.POST_POLL_MAX_CHECKS and time.monotonic() < deadline:
            checks += 1
            url = await _safe_current_url(browser)
            if url and _is_facebook_permalink(url):
                return "permalink"
            if await self._first_visible(
                browser, C.POST_SUCCESS_TEXT_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
            ):
                return "confirmed"
            # Composer Post button gone → post submitted and the composer closed.
            if not await self._submit_control_present(browser):
                return "dialog_closed"
            await asyncio.sleep(C.POST_POLL_INTERVAL_S)
        url = await _safe_current_url(browser)
        if url and _is_facebook_permalink(url):
            return "permalink"
        if not await self._submit_control_present(browser):
            return "dialog_closed"
        return None


# ── Module helpers ────────────────────────────────────────────────────────────

def _as_semantic_browser(adapter: object) -> Optional[SemanticBrowser]:
    return adapter if isinstance(adapter, SemanticBrowser) else None


def _post_from_instruction(instruction: PublishInstruction) -> dict:
    meta = instruction.meta or {}
    return {
        "text": instruction.text or "",
        "media_paths": list(meta.get("facebook_media_paths", []) or []),
        "target_type": meta.get("facebook_target_type", C.DEFAULT_TARGET_TYPE) or C.DEFAULT_TARGET_TYPE,
        "target_identifier": meta.get("facebook_target_identifier", "") or "",
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


def _is_facebook_permalink(url: str) -> bool:
    """True only for an absolute facebook.com URL that looks like a post permalink."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    if not any(host == h or host.endswith("." + h) for h in C.FACEBOOK_HOSTS):
        return False
    full = (parsed.path or "") + "?" + (parsed.query or "")
    return any(marker in full for marker in C.PERMALINK_MARKERS)


def _permalink_id(url: str) -> Optional[str]:
    """
    Extract a best-effort Facebook post id from a permalink URL.

    Handles /posts/<id>, /permalink/<id>, and story_fbid=<id>. Returns None when
    no id segment is present.
    """
    if not url:
        return None
    from urllib.parse import urlparse, parse_qs

    try:
        parsed = urlparse(url)
    except Exception:
        return None
    qs = parse_qs(parsed.query or "")
    if "story_fbid" in qs and qs["story_fbid"]:
        return qs["story_fbid"][0]
    parts = [p for p in (parsed.path or "").split("/") if p]
    for marker in ("posts", "permalink"):
        if marker in parts:
            i = parts.index(marker)
            if i + 1 < len(parts):
                return parts[i + 1]
    return None


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
