"""Semantic browser interface (real Hermes execution boundary).

This is the fine-grained, accessibility-first browser contract that real
Hermes platform workflows (e.g. Reddit) use. It sits alongside the coarse
`HermesExecutionAdapter` (connect/publish/verify/close) so existing code and
tests are untouched.

Design rules:
  - Interactions are SEMANTIC (by ARIA role + accessible name / label / test
    id), never by pixel coordinates.
  - Every method is bounded by a timeout; there are no infinite waits.
  - Workflows depend ONLY on this Protocol. The concrete driver (Playwright,
    or a future dedicated Hermes runtime) lives behind it. Swapping the driver
    must not require changing any workflow.

No Playwright import here — this module is pure interface + error types, so it
imports cleanly even when the browser runtime is not installed.
"""

from typing import Optional, Protocol, runtime_checkable


class BrowserError(Exception):
    """Base class for semantic-browser failures."""


class BrowserTimeout(BrowserError):
    """A bounded wait elapsed without the expected state."""


class ElementNotFound(BrowserError):
    """A semantic element (role + name) could not be located."""


class NavigationError(BrowserError):
    """Navigation did not reach the expected location/state."""


@runtime_checkable
class SemanticBrowser(Protocol):
    """
    Accessibility-first browser operations used by real Hermes workflows.

    Locators are expressed as (role, name) pairs — e.g. role="button",
    name="Post" — mirroring the accessibility tree. Implementations should
    prefer get_by_role / get_by_label / get_by_test_id semantics and must never
    rely on fragile pixel coordinates.
    """

    # ── Navigation ────────────────────────────────────────────────────────────

    async def goto(self, url: str, *, timeout_s: Optional[float] = None) -> None:
        """Navigate to a URL and wait for the page to settle."""
        ...

    async def current_url(self) -> str:
        """Return the current page URL."""
        ...

    # ── Reading state ───────────────────────────────────────────────────────────

    async def is_visible(
        self, role: str, name: str, *, timeout_s: Optional[float] = None
    ) -> bool:
        """Whether a semantic element is present and visible (bounded wait)."""
        ...

    async def get_text(
        self, role: str, name: str, *, timeout_s: Optional[float] = None
    ) -> str:
        """Return the text content of a semantic element."""
        ...

    async def wait_for(
        self, role: str, name: str, *, timeout_s: Optional[float] = None
    ) -> None:
        """Wait until a semantic element is present, or raise BrowserTimeout."""
        ...

    async def wait_for_url_contains(
        self, fragment: str, *, timeout_s: Optional[float] = None
    ) -> None:
        """Wait until the URL contains a fragment, or raise BrowserTimeout."""
        ...

    # ── Interactions ────────────────────────────────────────────────────────────

    async def fill(
        self, role: str, name: str, value: str, *, timeout_s: Optional[float] = None
    ) -> None:
        """Type a value into a semantic input/textbox."""
        ...

    async def click(
        self, role: str, name: str, *, timeout_s: Optional[float] = None
    ) -> None:
        """Click a semantic element (button/link/etc.)."""
        ...

    async def set_input_files(
        self, label: str, file_paths: list[str], *, timeout_s: Optional[float] = None
    ) -> None:
        """
        Attach files to a file input.

        `label` may be an accessible label/testid OR a CSS selector such as
        "input[type=file]" (implementations should accept both, since some
        current UIs do not expose the file input via an accessible label).
        """
        ...

    async def count_attached_files(
        self, selector: str = "input[type=file]", *, timeout_s: Optional[float] = None
    ) -> int:
        """
        Return how many files the matching file input currently holds.

        Read-only verification that an upload attached. Returns 0 when absent or
        empty. Must never upload or modify page state.
        """
        ...

    async def find_post_permalinks(
        self, title: str, *, timeout_s: Optional[float] = None
    ) -> list[str]:
        """
        Return distinct post permalink URLs on the CURRENT page whose link text
        matches `title` (verification fallback via a submitted-posts listing).

        Read-only: scans links only, never clicks/navigates/mutates. Returns
        absolute, de-duplicated URLs (empty list if none).
        """
        ...

    async def click_svg_anchor(
        self, svg_aria_label: str, *, timeout_s: Optional[float] = None
    ) -> bool:
        """
        Click the anchor that OWNS an icon-only control, resolved as
        `svg[aria-label=<svg_aria_label>].closest("a[href='#']")`.

        Needed for UIs (e.g. Instagram's sidebar/flyout) where the clickable
        element is an ancestor `<a href="#">` of a labelled SVG, and clicking the
        SVG itself does nothing. Clicks the FIRST match. Returns True if an
        anchor was found and clicked, False if none was found. Bounded wait.
        """
        ...

    async def is_css_visible(
        self, selector: str, *, timeout_s: Optional[float] = None
    ) -> bool:
        """
        Whether the FIRST element matching a raw CSS `selector` is visible within
        the bounded wait. Read-only. Complements is_visible(role, name) for cases
        where only a CSS/attribute selector identifies the element.
        """
        ...

    async def click_css(
        self, selector: str, *, timeout_s: Optional[float] = None
    ) -> bool:
        """
        Click the first ENABLED, visible element matching a raw CSS `selector`.

        For SPAs whose actionable control is a role-less / aria-labelled
        <div role="button"> that the (role, name) cascade cannot click reliably.
        Skips aria-disabled/disabled matches. Returns True if clicked, else False.
        Must never raise.
        """
        ...

    async def fill_css(
        self, selector: str, value: str, *, timeout_s: Optional[float] = None
    ) -> bool:
        """
        Type `value` into the first visible element matching a raw CSS `selector`.

        Handles native inputs/textareas AND contenteditable DIVs whose accessible
        name is exposed only via aria-placeholder (so the role/name cascade can't
        target them). Returns True on success, else False. Must never raise.
        """
        ...

    async def count_large_images(
        self, selector: str, min_px: int, *, timeout_s: Optional[float] = None
    ) -> int:
        """
        Count elements matching `selector` whose rendered width AND height are at
        least `min_px`. Read-only — used to confirm a sizeable attached-image
        preview while excluding small avatars/icons. Returns 0 on absence/error.
        """
        ...

    async def clear_css(
        self, selector: str, *, timeout_s: Optional[float] = None
    ) -> bool:
        """
        Clear the text of the first visible element matching a CSS `selector`
        (focus + select-all + delete). For rich contenteditable editors where a
        plain fill may not fully replace a restored draft. Returns True if a
        visible match was cleared, else False. Must never raise.
        """
        ...

    # ── Diagnostics ─────────────────────────────────────────────────────────────

    async def screenshot(self, path: str) -> None:
        """Capture a screenshot for diagnostics (never contains secrets by design)."""
        ...
