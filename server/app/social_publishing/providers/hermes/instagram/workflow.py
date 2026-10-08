"""HermesInstagramWorkflow — user-assisted Instagram publishing (single image).

Mirrors HermesRedditWorkflow. USER-ASSISTED, two-phase:

  Phase A — prepare():
    connect → verify authenticated session → open the create-post composer →
    attach ONE image → verify the image attached → advance through the
    crop/edit steps → enter the caption → confirm the Share button exists →
    STOP. Returns ACTION_REQUIRED. The final Share button is NOT clicked.

  Phase B — confirm_and_publish():  (only after explicit user confirmation)
    reuse the SAME browser session (composer still open) → click Share →
    verify the "post shared" confirmation / permalink → PUBLISHED. On
    uncertainty → UNKNOWN (never blind-retry, to avoid duplicate posts).

Talks ONLY to the SemanticBrowser interface — never imports Playwright, never
bypasses login/MFA/CAPTCHA. If Instagram requires auth or human verification,
the workflow returns ACTION_REQUIRED and lets the user handle it.

`execute()` runs Phase A only and returns ACTION_REQUIRED, so a scheduled job
pauses for the user instead of publishing silently (same as Reddit).
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
from app.social_publishing.providers.hermes.instagram import constants as C
from app.social_publishing.providers.hermes.instagram.validation import validate_post
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


class HermesInstagramWorkflow:
    """Instagram user-assisted publishing workflow (implements HermesPlatformWorkflow)."""

    # ── HermesPlatformWorkflow contract ─────────────────────────────────────────

    @property
    def platform(self) -> str:
        return C.PLATFORM

    @property
    def automation_allowed(self) -> bool:
        # Automation IS permitted for Instagram — but only user-assisted, and
        # only when the central PlatformAutomationPolicy ALSO allow-lists it.
        return True

    @property
    def requires_user_action(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        # This phase supports a single IMAGE post with an optional caption.
        # No text-only (Instagram requires media), no video/reels/carousel.
        return ProviderCapabilities(
            text=False,
            image=True,
            video=C.SUPPORTS_VIDEO,   # False
            links=False,
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
        ACTION_REQUIRED — Instagram is user-assisted and never auto-publishes in
        the background (same guarantee as Reddit).
        """
        browser = _as_semantic_browser(adapter)
        if browser is None:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.ACTION_REQUIRED,
                error_code=ExternalErrorCode.ACTION_REQUIRED,
                error_message="Instagram posting requires your confirmation in the browser.",
            )
        return await self.prepare(instruction, browser)

    # ── Phase A: prepare (stops at ACTION_REQUIRED) ─────────────────────────────

    async def prepare(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Prepare the Instagram post up to (but NOT including) the final Share.

        Returns ACTION_REQUIRED on success. Any authentication requirement also
        returns ACTION_REQUIRED (the user completes login/MFA in the browser).
        Validation failures return FAILED (no browser action taken).
        """
        post = _post_from_instruction(instruction)
        parsed, errors = validate_post(post["caption"], post.get("media_paths", []))
        if errors:
            return _failed(f"Invalid Instagram post: {'; '.join(errors)}",
                           ExternalErrorCode.EXTERNAL_CONTENT_REJECTED, retryable=False)

        # Validate the actual image file on disk BEFORE opening the browser.
        media_error = _validate_image_files(parsed.media_paths)
        if media_error:
            return _failed(f"Invalid Instagram image: {media_error}",
                           ExternalErrorCode.EXTERNAL_CONTENT_REJECTED, retryable=False)

        try:
            # 1. Verify authenticated session.
            if not await self._is_authenticated(browser):
                await browser.goto(C.LOGIN_URL, timeout_s=HERMES_NAVIGATION_TIMEOUT)
                return ProviderResult(
                    success=False,
                    status=ExternalResultStatus.ACTION_REQUIRED,
                    error_code=ExternalErrorCode.ACTION_REQUIRED,
                    error_message="Instagram login required — complete sign-in (and MFA) in the browser",
                )

            # 2. Open the create-post composer (Create → Post → reveal uploader).
            if not await self._open_composer(browser):
                return _failed("Could not open the Instagram composer", retryable=True)

            # 3. Wait for the (hidden) file input to mount, then attach the image
            #    via the real input and verify. The composer mounts the input
            #    lazily, so we poll for its DOM presence before attaching.
            if not await self._wait_for_file_input(browser):
                return _failed("Instagram media uploader did not appear", retryable=True)
            await browser.set_input_files(
                C.IMAGE_FILE_INPUT_SELECTOR, parsed.media_paths, timeout_s=HERMES_ACTION_TIMEOUT
            )
            if not await self._image_attached(browser):
                return _failed("Could not attach the image to the Instagram post", retryable=True)

            # 4. Advance through crop/edit steps until the caption field appears.
            await self._advance_to_caption(browser)

            # 5. Enter the caption (optional).
            if parsed.caption:
                filled = await self._fill_caption(browser, parsed.caption)
                if not filled:
                    return _failed("Could not enter the Instagram caption", retryable=True)

            # 6. Confirm the Share button exists (prepared) — but DO NOT click it.
            if not await self._first_visible(browser, C.LOC_SHARE_CANDIDATES, timeout_s=HERMES_ACTION_TIMEOUT):
                return _failed("Instagram Share control not found", retryable=True)

            # 7. STOP. Hand control back to the user for explicit confirmation.
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.ACTION_REQUIRED,
                error_code=ExternalErrorCode.ACTION_REQUIRED,
                error_message="Your Instagram post is ready. Confirm publishing.",
            )

        except (BrowserTimeout, NavigationError, BrowserError) as e:
            log.warning("Instagram prepare failed", op=instruction.operation_id, err=type(e).__name__)
            return _failed("Could not prepare the Instagram post", retryable=True)

    # ── Phase B: confirm + publish (after explicit user confirmation) ───────────

    async def confirm_and_publish(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Resume the SAME session (composer still present) and Share.

        The Share button is clicked here — only reached after the user
        explicitly confirmed. Then verify the shared confirmation / permalink. On
        any uncertainty after the click, return UNKNOWN (never blind-retry).
        """
        # Click Share. After this point the post MAY exist even if we lose the
        # result, so failures here are UNKNOWN, not FAILED.
        clicked = await self._first_visible(
            browser, C.LOC_SHARE_CANDIDATES, timeout_s=HERMES_ACTION_TIMEOUT, click=True
        )
        if not clicked:
            # The Share control was not found/clicked → nothing was submitted.
            return _failed("Could not submit the Instagram post", retryable=True)

        return await self._verify(instruction, browser)

    async def composer_ready(self, browser: SemanticBrowser) -> bool:
        """
        True if a REUSED (kept-alive) composer is still in the prepared state and
        safe to Share directly — i.e. the Share control is still visible. Used by
        the service to decide whether it can skip the re-prepare on confirm.
        Never raises; a False result just means "re-prepare instead".
        """
        try:
            return await self._first_visible(
                browser, C.LOC_SHARE_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
            )
        except Exception:
            return False

    async def verify(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """Public verify entry (used for UNKNOWN → VERIFYING re-checks)."""
        return await self._verify(instruction, browser)

    async def _verify(
        self, instruction: PublishInstruction, browser: SemanticBrowser
    ) -> ProviderResult:
        """
        Verify the share succeeded.

        Signal order (strongest deterministic first):
          1. The address bar becomes a post permalink (/p/<shortcode>/).
          2. The "Your post has been shared." confirmation appears.
        Either is sufficient evidence of PUBLISHED. Absence of both after the
        bounded wait → UNKNOWN (never blind-retried). Never clicks Share.
        """
        post = _post_from_instruction(instruction)
        try:
            outcome = await self._await_share_result(browser)
            if outcome == "permalink":
                url = await _safe_current_url(browser)
                if url and _is_instagram_permalink(url):
                    return ProviderResult(
                        success=True,
                        status=ExternalResultStatus.PUBLISHED,
                        external_post_id=_permalink_id(url),
                        external_url=url,
                    )
            if outcome in ("confirmed", "dialog_closed"):
                # Confirmation text seen (or the composer's Share control vanished
                # after the click — the reliable composer-closed signal) but no
                # canonical URL in the bar. Try to reconcile the permalink from
                # the user's profile grid (read-only).
                url = await self._find_recent_permalink(browser, post)
                return ProviderResult(
                    success=True,
                    status=ExternalResultStatus.PUBLISHED,
                    external_post_id=_permalink_id(url) if url else None,
                    external_url=url,
                )
            # No confirmation and no permalink → uncertain.
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.UNKNOWN,
                error_code=ExternalErrorCode.EXTERNAL_PUBLISH_UNKNOWN,
                error_message="Instagram publish result is uncertain — verification required",
                retryable=False,
            )
        except BrowserError:
            return ProviderResult(
                success=False,
                status=ExternalResultStatus.FAILED,
                error_code=ExternalErrorCode.EXTERNAL_VERIFICATION_FAILED,
                error_message="Could not verify the Instagram post",
                retryable=False,
            )

    # ── Internal helpers ────────────────────────────────────────────────────────

    async def _is_authenticated(self, browser: SemanticBrowser) -> bool:
        """Detect an existing authenticated Instagram session (no credentials typed)."""
        try:
            await browser.goto(C.HOME_URL, timeout_s=HERMES_NAVIGATION_TIMEOUT)
        except BrowserError:
            return False
        # Authenticated when a Create/New-post control is present AND no login
        # form is showing.
        authed = await self._first_visible(
            browser, C.LOC_AUTH_SIGNAL_CANDIDATES, timeout_s=HERMES_ACTION_TIMEOUT
        )
        if not authed:
            return False
        login_visible = await self._first_visible(
            browser, C.LOC_LOGIN_SIGNAL_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        )
        return not login_visible

    async def _open_composer(self, browser: SemanticBrowser) -> bool:
        """
        Open the create-post composer via the CONFIRMED live UI path and verify.

        Confirmed by live diagnostics:
          1. Sidebar "New post" is an icon whose click is owned by an ancestor
             <a href="#">: svg[aria-label="New post"].closest("a[href='#']").
             Clicking it opens the Create FLYOUT (plain <div>s, not an ARIA menu).
          2. The flyout's "Post" item is likewise
             svg[aria-label="Post"].closest("a[href='#']") — distinct from decoy
             profile "Post" links. Clicking it opens the composer MODAL.
          3. The composer is a dialog labelled "Create new post" with a
             "Select from computer" control (file input mounts after engaging it).

        We use the svg.closest(a[href='#']) resolution (never a generic
        get_by_role("link","Post")), bounded waits, and verify with robust
        indicators before returning True.
        """
        # 1. Click the sidebar New Post anchor → opens the Create flyout.
        clicked_new_post = await browser.click_svg_anchor(
            C.NEW_POST_SVG_LABEL, timeout_s=HERMES_ACTION_TIMEOUT
        )
        if not clicked_new_post:
            # Fallback to legacy nav candidates only if the confirmed control is
            # absent (older UI). Not a decoy-prone generic "Post" lookup.
            if not await self._first_visible(
                browser, C.LOC_CREATE_CANDIDATES, timeout_s=C.PROBE_TIMEOUT, click=True
            ):
                return False

        # 2. Click the flyout "Post" item → opens the composer modal. Retry the
        #    pair a bounded number of times, since the flyout needs a beat.
        for _ in range(3):
            clicked_post = await browser.click_svg_anchor(
                C.POST_FLYOUT_SVG_LABEL, timeout_s=HERMES_ACTION_TIMEOUT
            )
            if clicked_post and await self._composer_open(browser):
                return True
            if not clicked_post:
                # Flyout may not be rendered yet / closed — re-open and retry.
                await browser.click_svg_anchor(C.NEW_POST_SVG_LABEL, timeout_s=C.PROBE_TIMEOUT)
                await asyncio.sleep(C.SHARE_POLL_INTERVAL_S)
        return await self._composer_open(browser)

    async def _composer_open(self, browser: SemanticBrowser) -> bool:
        """
        True if the create-post composer modal is open, using robust indicators
        confirmed by the live probe (any one is sufficient):
          - a dialog[aria-label="Create new post"] is visible, OR
          - the "Create new post" heading text is visible, OR
          - the "Select from computer" button is visible, OR
          - a file input has mounted.
        """
        # Strongest: the labelled create dialog.
        try:
            if await browser.is_css_visible(C.COMPOSER_DIALOG_SELECTOR, timeout_s=C.PROBE_TIMEOUT):
                return True
        except Exception:
            pass
        # Heading text "Create new post" / "Crop".
        if await self._first_visible(
            browser, C.LOC_COMPOSER_DIALOG_TITLE_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        ):
            return True
        # "Select from computer" affordance.
        if await self._first_visible(
            browser, C.LOC_SELECT_FROM_COMPUTER_SIGNAL, timeout_s=C.PROBE_TIMEOUT
        ):
            return True
        # Hidden file input mounted inside the modal.
        try:
            if await browser.count_attached_files(
                C.IMAGE_FILE_INPUT_SELECTOR, timeout_s=C.PROBE_TIMEOUT
            ) >= 1:
                return True
        except Exception:
            pass
        return False

    async def _wait_for_file_input(self, browser: SemanticBrowser) -> bool:
        """
        Poll for the composer's (hidden) <input type=file> to mount, bounded.

        The current Instagram composer mounts the file input lazily inside the
        create-post modal, so a single immediate check can miss it. Uses
        count_attached_files on the file-input selector (a DOM count that ignores
        visibility, since the input is hidden). Read-only — never clicks.
        """
        for _ in range(C.SHARE_POLL_MAX_CHECKS):
            try:
                if await browser.count_attached_files(
                    C.IMAGE_FILE_INPUT_SELECTOR, timeout_s=C.PROBE_TIMEOUT
                ) >= 1:
                    return True
            except Exception:
                pass
            await asyncio.sleep(C.SHARE_POLL_INTERVAL_S)
        # Final check.
        try:
            return await browser.count_attached_files(
                C.IMAGE_FILE_INPUT_SELECTOR, timeout_s=C.PROBE_TIMEOUT
            ) >= 1
        except Exception:
            return False

    async def _image_attached(self, browser: SemanticBrowser) -> bool:
        """
        Confirm the image attached, using any ONE of the signals the live
        composer actually produces after a successful media attach (bounded,
        read-only — never clicks Next/Share here):

          1. A blob:/data: crop-preview <img> rendered (classic signal).
          2. A blob-backed CSS background preview (current composer renders the
             crop as `background-image: url('blob:…')`, not an <img>). Confirmed
             by live diagnostics (bg_blob=1).
          3. The composer's "Next" button has become visible — Instagram only
             surfaces Next AFTER media is attached, so a visible Next is a valid
             post-attach signal.

        The mere presence of an input[type=file] is NOT accepted as success (that
        exists before any attach). Absence of all three signals → False, so the
        existing fail-safe behavior is preserved.
        """
        # 1. blob:/data: <img> preview.
        try:
            if await browser.count_attached_files(
                C.IMAGE_PREVIEW_SELECTOR, timeout_s=HERMES_ACTION_TIMEOUT
            ) >= 1:
                return True
        except Exception:
            pass
        # 2. blob-backed CSS background preview.
        try:
            if await browser.count_attached_files(
                C.IMAGE_PREVIEW_BG_SELECTOR, timeout_s=C.PROBE_TIMEOUT
            ) >= 1:
                return True
        except Exception:
            pass
        # 3. Visible "Next" button (post-attach affordance) — read-only check.
        if await self._first_visible(
            browser, C.LOC_NEXT_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        ):
            return True
        return False

    async def _advance_to_caption(self, browser: SemanticBrowser) -> None:
        """Click Next until the caption field appears (bounded)."""
        for _ in range(3):  # crop → edit → caption is at most a few Next clicks
            if await self._first_visible(browser, C.LOC_CAPTION_CANDIDATES, timeout_s=C.PROBE_TIMEOUT):
                return
            if not await self._first_visible(
                browser, C.LOC_NEXT_CANDIDATES, timeout_s=C.PROBE_TIMEOUT, click=True
            ):
                break
        # Final short wait for the caption field (raises → caller treats as fail).
        # We don't hard-fail here; prepare() checks caption fill / Share presence.

    async def _fill_caption(self, browser: SemanticBrowser, caption: str) -> bool:
        for role, name in C.LOC_CAPTION_CANDIDATES:
            try:
                if await browser.is_visible(role, name, timeout_s=C.PROBE_TIMEOUT):
                    await browser.fill(role, name, caption, timeout_s=HERMES_ACTION_TIMEOUT)
                    return True
            except BrowserError:
                continue
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

    async def _share_control_present(self, browser: SemanticBrowser) -> bool:
        """
        FAST, SPECIFIC "is the composer still open?" probe used ONLY during
        verification — the composer's own Share control. Its presence means the
        post has NOT been accepted yet; its absence after the Share click is the
        reliable composer-closed success signal, mirroring the Facebook/Quora
        submit-button-gone check. Read-only; never clicks; never raises.
        """
        return await self._first_visible(
            browser, C.LOC_SHARE_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
        )

    async def _await_share_result(self, browser: SemanticBrowser) -> Optional[str]:
        """
        Poll for a share outcome, bounded. Returns one of:
          - 'permalink'     : URL became a post permalink (/p/…)
          - 'confirmed'     : the shared-confirmation text appeared
          - 'dialog_closed' : the composer's Share control disappeared after the
                              Share click — the reliable composer-closed signal
          - None            : none of the above within the bound (uncertain)

        Each attempt probes only fast, specific signals so an attempt is cheap
        (no full composer-open cascade).
        """
        deadline = time.monotonic() + HERMES_PUBLISH_TIMEOUT
        checks = 0
        while checks < C.SHARE_POLL_MAX_CHECKS and time.monotonic() < deadline:
            checks += 1
            url = await _safe_current_url(browser)
            if url and _is_instagram_permalink(url):
                return "permalink"
            if await self._first_visible(
                browser, C.POST_SHARED_TEXT_CANDIDATES, timeout_s=C.PROBE_TIMEOUT
            ):
                return "confirmed"
            # Share control gone → post submitted and the composer closed.
            if not await self._share_control_present(browser):
                return "dialog_closed"
            await asyncio.sleep(C.SHARE_POLL_INTERVAL_S)
        # Final read.
        url = await _safe_current_url(browser)
        if url and _is_instagram_permalink(url):
            return "permalink"
        if not await self._share_control_present(browser):
            return "dialog_closed"
        return None

    async def _find_recent_permalink(self, browser: SemanticBrowser, post: dict) -> Optional[str]:
        """
        Read-only reconciliation: open the user's profile and return the most
        recent post permalink if the username is known. Returns None on any
        ambiguity/absence (never guesses, never publishes).
        """
        username = (post.get("username") or "").strip()
        finder = getattr(browser, "find_post_permalinks", None)
        if not username or not callable(finder):
            return None
        try:
            await browser.goto(C.profile_url(username), timeout_s=HERMES_NAVIGATION_TIMEOUT)
            # Instagram post tiles link to /p/<shortcode>/ but carry no title; we
            # match on the permalink marker only. We accept a single most-recent
            # candidate only when exactly one distinct /p/ link is found near the
            # top — otherwise stay unknown.
            candidates = await finder(C.PERMALINK_MARKER, timeout_s=HERMES_ACTION_TIMEOUT)
        except BrowserError:
            return None
        valid = []
        for url in candidates or []:
            if _is_instagram_permalink(url) and url not in valid:
                valid.append(url)
        return valid[0] if len(valid) == 1 else None


# ── Module helpers ────────────────────────────────────────────────────────────

def _as_semantic_browser(adapter: object) -> Optional[SemanticBrowser]:
    return adapter if isinstance(adapter, SemanticBrowser) else None


def _post_from_instruction(instruction: PublishInstruction) -> dict:
    meta = instruction.meta or {}
    return {
        "caption": instruction.text or "",
        "media_paths": list(meta.get("instagram_media_paths", []) or []),
        "username": meta.get("instagram_username", "") or "",
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


def _is_instagram_permalink(url: str) -> bool:
    """True only for an absolute instagram.com URL whose path contains /p/."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    if not any(host == h or host.endswith("." + h) for h in C.INSTAGRAM_HOSTS):
        return False
    return C.PERMALINK_MARKER in (parsed.path or "")


def _permalink_id(url: str) -> Optional[str]:
    """Extract the Instagram shortcode from /p/<shortcode>/ (query stripped)."""
    if not url:
        return None
    path = url.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in path.split("/") if p]
    if "p" in parts:
        i = parts.index("p")
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
