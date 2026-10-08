"""HermesRedditWorkflow — the first REAL Hermes platform workflow (Reddit).

USER-ASSISTED, two-phase:

  Phase A — prepare():
    connect → verify authenticated session → navigate to the target subreddit's
    composer → select post type → enter title/body → attach media →
    PREPARE and STOP. Returns ACTION_REQUIRED. The final Post button is NOT
    clicked.

  Phase B — confirm_and_publish():  (only after explicit user confirmation)
    reuse the SAME browser session (still holding the prepared post) → click
    Post → verify the resulting permalink → PUBLISHED. On uncertainty →
    UNKNOWN (never blind-retry).

The workflow talks ONLY to the SemanticBrowser interface. It never imports
Playwright or touches a browser directly. It never bypasses login/MFA/CAPTCHA.

`execute()` (the HermesPlatformWorkflow Protocol method) runs Phase A only and
returns ACTION_REQUIRED, so a scheduled job pauses for the user instead of
publishing silently.
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
from app.social_publishing.providers.hermes.reddit import constants as C
from app.social_publishing.providers.hermes.reddit.validation import validate_post
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


class HermesRedditWorkflow:
    """Reddit user-assisted publishing workflow (implements HermesPlatformWorkflow)."""

    # ── HermesPlatformWorkflow contract ─────────────────────────────────────────

    @property
    def platform(self) -> str:
        return C.PLATFORM

    @property
    def automation_allowed(self) -> bool:
        # Automation IS permitted for Reddit — but only user-assisted (see the
        # central PlatformAutomationPolicy allow-list, which must ALSO permit it).
        return True

    @property
    def requires_user_action(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        # Only claim what the workflow actually implements/verifies (Phase 13):
        # text + image. Video is NOT reliably verifiable yet → video=False.
        return ProviderCapabilities(
            text=True,
            image=True,
            video=C.SUPPORTS_VIDEO,   # False — not reliably verifiable yet
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

        The real browser used here must expose the SemanticBrowser interface.
        Publishing is completed later via confirm_and_publish() after the user
        explicitly confirms.
        """
        browser = _as_semantic_browser(adapter)
        if browser is None:
            # Scheduled-job path: no live semantic browser is (or should be)
            # driven autonomously in the background. Reddit is user-assisted, so
            # we signal ACTION_REQUIRED — the user completes preparation +
            # explicit confirmation via the Reddit routes (which DO drive a real
            # browser). This is exactly Phase 12: a scheduled Reddit job never
            # silently publishes; it surfaces "action required".
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.ACTION_REQUIRED,
                error_code=ExternalErrorCode.ACTION_REQUIRED,
                error_message="Reddit posting requires your confirmation in the browser.",
            )
        return await self.prepare(instruction, browser)

    # ── Phase A: prepare (stops at ACTION_REQUIRED) ─────────────────────────────

    async def prepare(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Prepare the Reddit post up to (but NOT including) the final submit.

        Returns ACTION_REQUIRED on success. Any authentication requirement also
        returns ACTION_REQUIRED (the user must complete login/MFA in the
        browser). Validation failures return FAILED (no browser action taken).
        """
        post = _post_from_instruction(instruction)
        parsed, errors = validate_post(
            post["title"], post["body"], post["subreddit"], post.get("media_paths", [])
        )
        if errors:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_CONTENT_REJECTED,
                error_message=f"Invalid Reddit post: {'; '.join(errors)}",
                retryable=False,
            )

        # Image posts: validate the actual file on disk (existence + type + size)
        # BEFORE opening the browser, reusing the restricted media workspace's
        # validator. An invalid/missing/oversize image never launches automation.
        if parsed.media_paths:
            media_error = _validate_image_files(parsed.media_paths)
            if media_error:
                return ProviderResult(
                    success=False,
                    status=ExternalResultStatus.FAILED,
                    error_code=ExternalErrorCode.EXTERNAL_CONTENT_REJECTED,
                    error_message=f"Invalid Reddit image: {media_error}",
                    retryable=False,
                )

        try:
            # 1. Verify authenticated session (reuse existing browser session).
            if not await self._is_authenticated(browser):
                # 2. Not logged in → user must authenticate in the browser.
                #    We navigate to login and STOP; we never type credentials.
                await browser.goto(C.LOGIN_URL, timeout_s=HERMES_NAVIGATION_TIMEOUT)
                return ProviderResult(
                    success=False,
                    status=ExternalResultStatus.ACTION_REQUIRED,
                    error_code=ExternalErrorCode.ACTION_REQUIRED,
                    error_message="Reddit login required — complete sign-in (and MFA) in the browser",
                )

            # 3. Navigate to the target subreddit's composer.
            await browser.goto(
                C.submit_url(parsed.subreddit), timeout_s=HERMES_NAVIGATION_TIMEOUT
            )

            # 4. Subreddit safety check (Phase 14): confirm we're on the intended
            #    subreddit before entering anything.
            current = (await browser.current_url()).lower()
            if f"/r/{parsed.subreddit.lower()}/" not in current:
                return ProviderResult(
                    success=False,
                    status=ExternalResultStatus.FAILED,
                    error_code=ExternalErrorCode.EXTERNAL_CONTENT_REJECTED,
                    error_message="Reached an unexpected subreddit — not publishing",
                    retryable=False,
                )

            # 5. Select post type + enter content.
            if parsed.media_paths:
                # Image post (single image). Select the image composer for the
                # current UI (falls back to the legacy tab), attach the image via
                # the real file input, VERIFY it attached, then fill title/body.
                await self._ensure_image_composer(browser)
                await browser.set_input_files(
                    C.IMAGE_FILE_INPUT_SELECTOR, parsed.media_paths,
                    timeout_s=HERMES_ACTION_TIMEOUT,
                )
                if not await self._image_attached(browser):
                    # The upload did not register → do not proceed to a post that
                    # would be missing its image. Safe to fail (nothing submitted).
                    return ProviderResult(
                        success=False,
                        status=ExternalResultStatus.FAILED,
                        error_code=ExternalErrorCode.EXTERNAL_PROVIDER_UNAVAILABLE,
                        error_message="Could not attach the image to the Reddit post",
                        retryable=True,
                    )
                await browser.fill(*C.LOC_TITLE_INPUT, parsed.title, timeout_s=HERMES_ACTION_TIMEOUT)
                if parsed.body:
                    await browser.fill(*C.LOC_BODY_INPUT, parsed.body, timeout_s=HERMES_ACTION_TIMEOUT)
            else:
                # Reddit's current composer defaults to a text post and no longer
                # exposes an ARIA tab named "Text". Keep backward compatibility:
                # click the legacy Text tab IF it is present; otherwise verify the
                # text composer is already active (Title textbox present) and
                # continue. This never bypasses any safety check — it only selects
                # (or confirms) the text-post mode.
                await self._ensure_text_composer(browser)
                await browser.fill(*C.LOC_TITLE_INPUT, parsed.title, timeout_s=HERMES_ACTION_TIMEOUT)
                if parsed.body:
                    await browser.fill(*C.LOC_BODY_INPUT, parsed.body, timeout_s=HERMES_ACTION_TIMEOUT)

            # 6. Confirm the Post button exists (prepared) — but DO NOT click it.
            await browser.wait_for(*C.LOC_POST_BUTTON, timeout_s=HERMES_ACTION_TIMEOUT)

            # 7. STOP. Hand control back to the user for explicit confirmation.
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.ACTION_REQUIRED,
                error_code=ExternalErrorCode.ACTION_REQUIRED,
                error_message="Your Reddit post is ready. Confirm publishing.",
            )

        except (BrowserTimeout, NavigationError, BrowserError) as e:
            # Preparation failed before any submit — safe to fail (no post made).
            log.warning("Reddit prepare failed", op=instruction.operation_id, err=type(e).__name__)
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_PROVIDER_UNAVAILABLE,
                error_message="Could not prepare the Reddit post",
                retryable=True,
            )

    # ── Phase B: confirm + publish (after explicit user confirmation) ───────────

    async def confirm_and_publish(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Resume the SAME session (prepared post still present) and submit.

        The final Post button is clicked here — only reached after the user
        explicitly confirmed. Then verify the resulting permalink. On any
        uncertainty after the click, return UNKNOWN (never blind-retry).
        """
        post = _post_from_instruction(instruction)
        subreddit = post["subreddit"]

        # Re-verify we are still on the intended subreddit composer (Phase 14).
        try:
            current = (await browser.current_url()).lower()
        except BrowserError:
            current = ""
        if subreddit and f"/r/{subreddit.lower()}/" not in current:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_CONTENT_REJECTED,
                error_message="Session is no longer on the intended subreddit — not publishing",
                retryable=False,
            )

        # Click Post. After this point the post MAY exist even if we lose the
        # result, so failures here are UNKNOWN, not FAILED.
        try:
            await browser.click(*C.LOC_POST_BUTTON, timeout_s=HERMES_ACTION_TIMEOUT)
        except BrowserError:
            # The click itself did not register → nothing was submitted.
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_PROVIDER_UNAVAILABLE,
                error_message="Could not submit the Reddit post",
                retryable=True,
            )

        # Verify: Reddit navigates to the created post permalink (/comments/...).
        return await self._verify(instruction, browser)

    async def verify(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """Public verify entry (used for UNKNOWN → VERIFYING re-checks)."""
        return await self._verify(instruction, browser)

    async def _verify(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Verify a submit succeeded by detecting the created post's permalink.

        Signal order (strongest deterministic first):
          1. The address bar becomes the created post's permalink
             (…/r/<sub>/comments/<id>/…). The current Reddit UI is a SPA and can
             take several seconds to make this transition, so we POLL
             current_url() across the publish timeout instead of relying on a
             single wait that may resolve before the redirect. This is the
             canonical, server-assigned URL — the strongest possible signal.

          2. FALLBACK — if the address bar never becomes a permalink (the
             current Reddit UI often does not redirect after submit), open the
             authenticated user's submitted-posts listing (read-only) and locate
             the just-created post by its exact title. This is the same signal a
             manual reconciliation uses, promoted into the normal flow.

        Any permalink used must be a trustworthy reddit.com URL AND belong to the
        EXPECTED subreddit (canonical validation). Only then → PUBLISHED. Any
        remaining ambiguity → UNKNOWN (never blind-retried). We never click Post
        here and never create another post.
        """
        post = _post_from_instruction(instruction)
        expected_subreddit = post.get("subreddit", "")
        try:
            url = await self._await_permalink(browser)
            if url and _is_expected_permalink(url, expected_subreddit):
                return ProviderResult(
                    success=True,
                    status=ExternalResultStatus.PUBLISHED,
                    external_post_id=_permalink_id(url),
                    external_url=url,
                )
            # Reached a /comments/ URL but it isn't a trustworthy permalink for
            # the expected subreddit → do NOT claim success.
            if url:
                return ProviderResult(
                    success=False,
                    status=ExternalResultStatus.UNKNOWN,
                    error_code=ExternalErrorCode.EXTERNAL_PUBLISH_UNKNOWN,
                    error_message="Reddit permalink did not match the expected destination",
                    retryable=False,
                )

            # ── Fallback: locate the post in the user's submitted listing ──────
            fallback_url = await self._find_in_submitted(browser, post)
            if fallback_url:
                return ProviderResult(
                    success=True,
                    status=ExternalResultStatus.PUBLISHED,
                    external_post_id=_permalink_id(fallback_url),
                    external_url=fallback_url,
                )

            # Never observed a permalink transition and no confident listing
            # match → uncertain.
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.UNKNOWN,
                error_code=ExternalErrorCode.EXTERNAL_PUBLISH_UNKNOWN,
                error_message="Reddit publish result is uncertain — verification required",
                retryable=False,
            )
        except BrowserError:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_VERIFICATION_FAILED,
                error_message="Could not verify the Reddit post",
                retryable=False,
            )

    async def _await_permalink(self, browser: SemanticBrowser) -> Optional[str]:
        """
        Poll the address bar until it becomes a post permalink, or give up.

        Returns the permalink URL (containing "/comments/") once observed, else
        None. Bounded by BOTH the publish timeout and a hard check cap, so it can
        never wait indefinitely. Reads only — never clicks or navigates.
        """
        deadline = time.monotonic() + HERMES_PUBLISH_TIMEOUT
        # First, give Reddit's own navigation a chance via the existing bounded
        # wait; if it fires we still re-read the URL below. A timeout here is
        # expected on the current SPA and simply falls through to polling.
        try:
            await browser.wait_for_url_contains(
                C.PERMALINK_MARKER, timeout_s=C.PERMALINK_POLL_INTERVAL_S
            )
        except BrowserTimeout:
            pass

        checks = 0
        while checks < C.PERMALINK_POLL_MAX_CHECKS and time.monotonic() < deadline:
            checks += 1
            try:
                url = await browser.current_url()
            except BrowserError:
                url = ""
            if url and C.PERMALINK_MARKER in url:
                return url
            await asyncio.sleep(C.PERMALINK_POLL_INTERVAL_S)
        # One final read in case the transition happened on the last tick.
        try:
            url = await browser.current_url()
        except BrowserError:
            url = ""
        return url if (url and C.PERMALINK_MARKER in url) else None

    async def _find_in_submitted(self, browser: SemanticBrowser, post: dict) -> Optional[str]:
        """
        Read-only fallback: find the just-created post in the user's submitted
        listing by exact title, and return its permalink if confident.

        Requires a username (from instruction meta) and a title. Navigates to
        /user/<username>/submitted/, collects permalinks whose link text matches
        the title, keeps only those that canonically belong to the expected
        subreddit, and returns the URL ONLY when exactly one distinct post
        matches. Zero or multiple matches → None (stay UNKNOWN — never guess).

        Never clicks Post, never creates a post. `find_post_permalinks` is a
        read-only scan.
        """
        username = (post.get("username") or "").strip()
        title = (post.get("title") or "").strip()
        subreddit = post.get("subreddit", "")
        if not username or not title:
            return None

        finder = getattr(browser, "find_post_permalinks", None)
        if not callable(finder):
            return None

        try:
            await browser.goto(C.submitted_url(username), timeout_s=HERMES_NAVIGATION_TIMEOUT)
            candidates = await finder(title, timeout_s=HERMES_ACTION_TIMEOUT)
        except BrowserError:
            return None

        # Keep only trustworthy permalinks for the expected subreddit, de-duped
        # by the /comments/<id> segment.
        valid: dict[str, str] = {}
        for url in candidates or []:
            if _is_expected_permalink(url, subreddit):
                valid[_permalink_id(url)] = url
        # Confident only when exactly one distinct post matches.
        if len(valid) == 1:
            return next(iter(valid.values()))
        return None

    # ── Internal ────────────────────────────────────────────────────────────────

    async def _ensure_text_composer(self, browser: SemanticBrowser) -> None:
        """
        Select the text-post composer for the current Reddit UI.

        Reddit changed its composer: the post-type "Text" ARIA tab no longer
        exists and text is the default mode. To stay compatible with BOTH the
        legacy tabbed composer and the current one:

          - If a legacy Text tab is present, TRY to click it (best-effort).
            The current Reddit UI can expose a visible "Text" tab element that
            passes an accessibility visibility check but is NOT clickable (the
            click raises ElementNotFound). A failed click is NOT fatal — the
            composer is already in text mode.
          - Regardless of whether the tab click succeeded, confirm the text
            composer is active by checking the Title textbox is present.

        Raises ElementNotFound (via wait_for) only if the Title textbox is NOT
        present — i.e. this is not a usable text composer at all. This method
        never clicks Post and never weakens any safety check.
        """
        # Short probe: a legacy Text tab, if it exists, renders immediately.
        if await browser.is_visible(*C.LOC_TEXT_TAB, timeout_s=C.TAB_PROBE_TIMEOUT):
            try:
                await browser.click(*C.LOC_TEXT_TAB, timeout_s=HERMES_ACTION_TIMEOUT)
            except BrowserError:
                # Tab visible but not clickable — the current Reddit UI exposes a
                # tab that can't be clicked. Not fatal: the composer defaults to
                # text mode. Fall through to the title-input check.
                pass
        # Verify the text composer is active (Title textbox present). wait_for
        # raises if the Title textbox never appears, which the caller treats as
        # a prepare failure (safe: nothing was submitted).
        await browser.wait_for(*C.LOC_TITLE_INPUT, timeout_s=HERMES_ACTION_TIMEOUT)

    async def _ensure_image_composer(self, browser: SemanticBrowser) -> None:
        """
        Select the image-post composer for the current Reddit UI.

        Reddit changed its composer: the post-type control is now a BUTTON named
        "Image" (the old ARIA "Images & Video" tab is gone). For compatibility
        with BOTH the current and legacy composers:

          - If the current "Image" button is present, click it.
          - Else if the legacy "Images & Video" tab is present, click it.
          - Else raise ElementNotFound — no usable image composer.

        This only selects the image-post mode. It never clicks Post and never
        weakens a safety check.
        """
        if await browser.is_visible(*C.LOC_IMAGE_BUTTON, timeout_s=C.TAB_PROBE_TIMEOUT):
            await browser.click(*C.LOC_IMAGE_BUTTON, timeout_s=HERMES_ACTION_TIMEOUT)
            return
        if await browser.is_visible(*C.LOC_IMAGE_TAB, timeout_s=C.TAB_PROBE_TIMEOUT):
            await browser.click(*C.LOC_IMAGE_TAB, timeout_s=HERMES_ACTION_TIMEOUT)
            return
        # Neither present → surface as a bounded wait failure (caller → FAILED).
        await browser.wait_for(*C.LOC_IMAGE_BUTTON, timeout_s=HERMES_ACTION_TIMEOUT)

    async def _image_attached(self, browser: SemanticBrowser) -> bool:
        """
        Confirm at least one image actually attached to the composer.

        The current Reddit composer is a web component that consumes the file and
        clears the native <input>.files, so we do NOT rely on files.length.
        Instead:
          primary  — an accessible "Remove media" control appears once an image
                     is attached,
          fallback — a blob preview <img> is rendered per attached image.
        Either signal is sufficient; absence of both means the attach failed.
        """
        if await browser.is_visible(*C.LOC_REMOVE_MEDIA, timeout_s=HERMES_ACTION_TIMEOUT):
            return True
        try:
            previews = await browser.count_attached_files(
                C.IMAGE_PREVIEW_SELECTOR, timeout_s=HERMES_ACTION_TIMEOUT
            )
        except Exception:
            previews = 0
        return previews >= 1

    async def _is_authenticated(self, browser: SemanticBrowser) -> bool:
        """Detect an existing authenticated Reddit session (no credentials typed)."""
        try:
            await browser.goto(C.REDDIT_BASE, timeout_s=HERMES_NAVIGATION_TIMEOUT)
            return await browser.is_visible(*C.LOC_USER_MENU, timeout_s=HERMES_ACTION_TIMEOUT)
        except BrowserError:
            return False


# ── Helpers ─────────────────────────────────────────────────────────────────

def _as_semantic_browser(adapter: object) -> Optional[SemanticBrowser]:
    """Return the adapter if it satisfies the SemanticBrowser interface."""
    return adapter if isinstance(adapter, SemanticBrowser) else None


def _post_from_instruction(instruction: PublishInstruction) -> dict:
    """
    Extract the Reddit post fields from the instruction.

    Reddit needs title + subreddit in addition to body/media. These ride in
    instruction.meta (set when the job is created), with text as the body.
    """
    meta = instruction.meta or {}
    return {
        "title": meta.get("reddit_title", "") or "",
        "body": instruction.text or "",
        "subreddit": meta.get("reddit_subreddit", "") or "",
        "media_paths": list(meta.get("reddit_media_paths", []) or []),
        "username": meta.get("reddit_username", "") or "",
    }


def _permalink_id(url: str) -> str:
    """Extract the Reddit post id from a permalink URL (…/comments/<id>/…).

    Strips any query string/fragment so ids like "abc123?entry_point=…" are
    returned as just "abc123".
    """
    path = url.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in path.split("/") if p]
    if "comments" in parts:
        idx = parts.index("comments")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return url


def _validate_image_files(media_paths: list[str]) -> Optional[str]:
    """
    Validate image files on disk before any browser action.

    For each path: it must exist, be a regular file, and pass the restricted
    media workspace validator (allowed type + within the size limit). Returns an
    error string on the first problem, or None if all files are valid.

    Reuses media_workspace.validate_media so image constraints live in one place.
    Does not copy or move anything and never exposes full local paths in the
    returned message (only the base filename).
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


def _is_expected_permalink(url: str, expected_subreddit: str) -> bool:
    """
    True only if `url` is a trustworthy Reddit post permalink for the EXPECTED
    subreddit (canonical validation).

    Requirements:
      - absolute http(s) URL on a reddit.com host,
      - path contains "/comments/",
      - path contains "/r/<expected_subreddit>/" (case-insensitive).

    Rejects relative URLs, non-reddit hosts, and permalinks for a DIFFERENT
    subreddit (e.g. an unrelated recommendation). If no expected subreddit was
    provided we still require a reddit.com /comments/ permalink, but cannot do
    the subreddit match — so we conservatively refuse (return False) to avoid
    claiming success on the wrong post.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    if not any(host == h or host.endswith("." + h) for h in C.REDDIT_HOSTS):
        return False
    path = (parsed.path or "").lower()
    if C.PERMALINK_MARKER not in path:
        return False
    sub = (expected_subreddit or "").strip().lower()
    if not sub:
        return False
    return f"/r/{sub}/" in path
