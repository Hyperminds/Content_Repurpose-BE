"""PlaywrightHermesAdapter — the REAL browser-execution engine behind Hermes.

Architecture:
    Trendzzo → HermesExecutionAdapter → PlaywrightHermesAdapter → Playwright/Chromium → Reddit

This is a REAL adapter, not a simulator. It implements the `SemanticBrowser`
interface using Playwright, driving a Chromium browser via accessibility-first
locators (get_by_role / get_by_label), never pixel coordinates.

REQUIREMENTS (not installed by default; see docs/architecture/hybrid-publishing.md):
    pip install playwright
    python -m playwright install chromium

Session reuse: a **persistent context** rooted at a per-account profile
directory keeps the authenticated Reddit session (and the prepared post's page)
alive between the prepare phase and the user-confirmed publish phase, without
storing any Reddit password or exposing cookies.

This module imports Playwright LAZILY inside `start()` so the rest of the app
(and all non-browser tests) import cleanly when Playwright is absent.

Nothing outside this file touches Playwright. If a dedicated Hermes runtime is
added later, write another SemanticBrowser adapter — the Reddit workflow and
the publishing engine do not change.
"""

import os
from typing import Optional

from app.services.logger import log
from app.social_publishing.providers.hermes.constants import (
    HERMES_ACTION_TIMEOUT,
    HERMES_CONNECTION_TIMEOUT,
    HERMES_NAVIGATION_TIMEOUT,
)
from app.social_publishing.providers.hermes.semantic_browser import (
    BrowserError,
    BrowserTimeout,
    ElementNotFound,
    NavigationError,
)

# Where per-account persistent browser profiles live (keeps sessions isolated).
_PROFILE_ROOT = os.getenv(
    "HERMES_BROWSER_PROFILE_DIR",
    os.path.join(os.getenv("TEMP", "/tmp"), "trendzzo_hermes_profiles"),
)


def _ms(timeout_s: Optional[float], default_s: float) -> float:
    """Convert a seconds timeout to Playwright milliseconds with a default."""
    return float((timeout_s if timeout_s is not None else default_s)) * 1000.0


def _css_quote(value: str) -> str:
    """Quote a string for use inside a CSS attribute selector ([aria-label=...])."""
    return '"' + (value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _comments_id(url: str) -> str:
    """Return the /comments/<id> segment of a Reddit URL, or "" if absent."""
    path = url.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in path.split("/") if p]
    if "comments" in parts:
        i = parts.index("comments")
        if i + 1 < len(parts):
            return parts[i + 1]
    return ""


class PlaywrightHermesAdapter:
    """Real Playwright-backed SemanticBrowser implementation."""

    def __init__(self, profile_key: str, *, headless: Optional[bool] = None) -> None:
        # profile_key isolates one account's session (tenant+account scoped by
        # the caller). Sanitized to a safe directory name.
        safe = "".join(c for c in profile_key if c.isalnum() or c in ("_", "-"))[:64] or "default"
        self._profile_dir = os.path.join(_PROFILE_ROOT, safe)
        # Headful by default so a human can complete login/MFA; env can override
        # for CI/headless smoke tests.
        env_headless = os.getenv("HERMES_HEADLESS")
        self._headless = headless if headless is not None else (env_headless == "true")
        self._pw = None
        self._context = None
        self._page = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Launch a persistent Chromium context and open a page.

        Playwright is imported here so the module loads without it installed.
        """
        try:
            from playwright.async_api import async_playwright  # lazy import
        except ImportError as e:
            raise BrowserError(
                "Playwright is not installed. Run: pip install playwright && "
                "python -m playwright install chromium"
            ) from e

        os.makedirs(self._profile_dir, exist_ok=True)
        self._pw = await async_playwright().start()
        try:
            # Persistent context = the profile dir holds cookies/session so an
            # authenticated Reddit login persists across prepare/confirm.
            self._context = await self._pw.chromium.launch_persistent_context(
                self._profile_dir,
                headless=self._headless,
            )
            self._context.set_default_timeout(_ms(None, HERMES_ACTION_TIMEOUT))
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
        except Exception as e:
            await self.close()
            raise BrowserError(f"Failed to launch browser: {type(e).__name__}") from e

    async def close(self) -> None:
        """Release the browser context and Playwright driver."""
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._pw = None

    def _require_page(self):
        if self._page is None:
            raise BrowserError("Browser not started — call start() first")
        return self._page

    # ── Navigation ────────────────────────────────────────────────────────────

    async def goto(self, url: str, *, timeout_s: Optional[float] = None) -> None:
        page = self._require_page()
        try:
            await page.goto(url, timeout=_ms(timeout_s, HERMES_NAVIGATION_TIMEOUT),
                            wait_until="domcontentloaded")
        except Exception as e:
            raise NavigationError(f"Navigation failed: {type(e).__name__}") from e

    async def current_url(self) -> str:
        return self._require_page().url

    # ── Reading state ───────────────────────────────────────────────────────────

    def _role_locator(self, role: str, name: str):
        """
        Build the primary role locator (exact accessible name, first match).

        `.first` avoids Playwright strict-mode failures when a name matches
        several elements; `exact=True` avoids "Post" also matching "New post".
        """
        page = self._require_page()
        loc = page.get_by_role(role, name=name, exact=True) if name else page.get_by_role(role)
        return loc.first

    async def _resolve_visible(self, role: str, name: str, *, timeout_s: Optional[float] = None):
        """
        Return the FIRST visible locator for (role, name), trying several
        strategies in order, or None if none is visible within the timeout.

        Instagram (and other SPAs) frequently render actionable items as
        role-less <div>/<span> with only visible TEXT, so a pure get_by_role
        lookup misses them even though the text is clearly present. The cascade:
          1. role + exact accessible name
          2. role + substring accessible name
          3. exact visible text (any element)
          4. substring visible text (any element)
        Each candidate is `.first` and checked for visibility. Read-only.
        """
        page = self._require_page()
        # Split the per-candidate budget so the whole cascade stays bounded.
        per = _ms(timeout_s, HERMES_ACTION_TIMEOUT) / 4.0
        candidates = []
        if name:
            candidates.append(page.get_by_role(role, name=name, exact=True))
            candidates.append(page.get_by_role(role, name=name))       # substring
            candidates.append(page.get_by_text(name, exact=True))
            candidates.append(page.get_by_text(name))                  # substring
        else:
            candidates.append(page.get_by_role(role))
        for loc in candidates:
            first = loc.first
            try:
                await first.wait_for(state="visible", timeout=per)
                return first
            except Exception:
                continue
        return None

    async def is_visible(self, role: str, name: str, *, timeout_s: Optional[float] = None) -> bool:
        return (await self._resolve_visible(role, name, timeout_s=timeout_s)) is not None

    async def get_text(self, role: str, name: str, *, timeout_s: Optional[float] = None) -> str:
        loc = await self._resolve_visible(role, name, timeout_s=timeout_s)
        if loc is None:
            raise ElementNotFound(f"{role}:{name}")
        try:
            return (await loc.inner_text(timeout=_ms(timeout_s, HERMES_ACTION_TIMEOUT))) or ""
        except Exception as e:
            raise ElementNotFound(f"{role}:{name}") from e

    async def wait_for(self, role: str, name: str, *, timeout_s: Optional[float] = None) -> None:
        loc = await self._resolve_visible(role, name, timeout_s=timeout_s)
        if loc is None:
            raise BrowserTimeout(f"Timed out waiting for {role}:{name}")

    async def wait_for_url_contains(self, fragment: str, *, timeout_s: Optional[float] = None) -> None:
        page = self._require_page()
        try:
            await page.wait_for_url(f"**{fragment}**", timeout=_ms(timeout_s, HERMES_NAVIGATION_TIMEOUT))
        except Exception as e:
            raise BrowserTimeout(f"URL never contained '{fragment}'") from e

    # ── Interactions ────────────────────────────────────────────────────────────

    async def fill(self, role: str, name: str, value: str, *, timeout_s: Optional[float] = None) -> None:
        loc = await self._resolve_visible(role, name, timeout_s=timeout_s)
        if loc is None:
            raise ElementNotFound(f"fill {role}:{name}")
        try:
            await loc.fill(value, timeout=_ms(timeout_s, HERMES_ACTION_TIMEOUT))
        except Exception as e:
            raise ElementNotFound(f"fill {role}:{name}") from e

    async def click(self, role: str, name: str, *, timeout_s: Optional[float] = None) -> None:
        loc = await self._resolve_visible(role, name, timeout_s=timeout_s)
        if loc is None:
            raise ElementNotFound(f"click {role}:{name}")
        try:
            await loc.click(timeout=_ms(timeout_s, HERMES_ACTION_TIMEOUT))
        except Exception as e:
            raise ElementNotFound(f"click {role}:{name}") from e

    async def set_input_files(self, label: str, file_paths: list[str], *, timeout_s: Optional[float] = None) -> None:
        page = self._require_page()
        timeout = _ms(timeout_s, HERMES_ACTION_TIMEOUT)
        # Two ways to locate the file input, tried in order:
        #   1. A raw CSS selector (label looks like "input[type=file]" / "input…")
        #      or the accessible-label lookup finds nothing — target the real
        #      <input type=file>. set_input_files works even on hidden inputs.
        #   2. The accessible label/testid (legacy composer).
        # This keeps backward compatibility while supporting the current UI,
        # whose file input is NOT exposed via an accessible label.
        looks_like_selector = label.strip().startswith("input") or "[" in label
        try:
            if looks_like_selector:
                locator = page.locator(label)
            else:
                by_label = page.get_by_label(label)
                # If the label matches nothing, fall back to the raw file input.
                locator = by_label if (await by_label.count()) > 0 else page.locator("input[type=file]")
            await locator.first.set_input_files(file_paths, timeout=timeout)
        except Exception as e:
            raise ElementNotFound(f"file input '{label}'") from e

    async def count_attached_files(self, selector: str = "img[src^='blob:']", *, timeout_s: Optional[float] = None) -> int:
        """
        Read-only: count elements matching `selector` — used to confirm an upload
        attached.

        The current Reddit composer is a web component that consumes the file and
        CLEARS the native <input>.files, so `input.files.length` is unreliable
        (reads 0 even on success). Instead we count the composer's blob preview
        images (`img[src^='blob:']`), which appear once per attached image. Any
        CSS selector may be passed. Returns 0 on absence/error. Never uploads.
        """
        page = self._require_page()
        try:
            # A short settle so the preview has time to render after attach.
            return int(await page.locator(selector).count())
        except Exception:
            return 0

    async def find_post_permalinks(self, title: str, *, timeout_s: Optional[float] = None) -> list[str]:
        """
        Read-only: collect distinct post permalink URLs on the CURRENT page whose
        title matches `title`.

        Primary source is Reddit's `shreddit-post` custom elements, which expose
        clean `post-title` + `permalink` attributes (the visible anchor text is
        truncated, so it is NOT reliable for long titles). Falls back to scanning
        `/comments/` anchors when no shreddit-post elements are present.

        Matching is truncation-aware: a listing title matches when it and the
        target title share a strong common prefix (either is a prefix of the
        other, min 40 chars or the shorter length). Scans only — never clicks,
        navigates, or mutates. Returns de-duplicated absolute URLs.
        """
        page = self._require_page()
        target = (title or "").strip().lower()
        if not target:
            return []

        def _matches(listing_title: str) -> bool:
            lt = (listing_title or "").strip().lower()
            if not lt:
                return False
            if lt == target:
                return True
            # Truncation-aware prefix match (Reddit truncates displayed titles).
            n = min(len(lt), len(target))
            if n < 15:
                return lt == target
            threshold = min(n, 40)
            return lt[:threshold] == target[:threshold] and (
                target.startswith(lt) or lt.startswith(target[: len(lt)])
            )

        found: dict[str, str] = {}
        try:
            posts = page.locator("shreddit-post")
            pcount = await posts.count()
            for i in range(min(pcount, 100)):
                el = posts.nth(i)
                try:
                    pt = await el.get_attribute("post-title")
                    perm = await el.get_attribute("permalink")
                except Exception:
                    continue
                if not perm or not _matches(pt or ""):
                    continue
                abs_url = perm if perm.startswith("http") else f"https://www.reddit.com{perm}"
                key = _comments_id(abs_url)
                if key and key not in found:
                    found[key] = abs_url
            if found:
                return list(found.values())

            # Fallback: anchor scan (older UI / no shreddit-post elements).
            anchors = page.locator("a[href*='/comments/']")
            count = await anchors.count()
            for i in range(min(count, 200)):
                a = anchors.nth(i)
                try:
                    href = await a.get_attribute("href")
                    text = ((await a.inner_text()) or "").strip()
                except Exception:
                    continue
                if not href or not _matches(text):
                    continue
                abs_url = href if href.startswith("http") else f"https://www.reddit.com{href}"
                key = _comments_id(abs_url)
                if key and key not in found:
                    found[key] = abs_url
        except Exception:
            return []
        return list(found.values())

    async def click_svg_anchor(self, svg_aria_label: str, *, timeout_s: Optional[float] = None) -> bool:
        page = self._require_page()
        # The owning anchor = a[href='#'] that CONTAINS svg[aria-label=<label>].
        # Playwright `filter(has=...)` expresses exactly svg.closest("a[href='#']")
        # at the anchor level. `.first` avoids strict-mode if duplicated.
        try:
            svg = page.locator(f"svg[aria-label={_css_quote(svg_aria_label)}]")
            anchor = page.locator("a[href='#']").filter(has=svg).first
            await anchor.wait_for(state="visible", timeout=_ms(timeout_s, HERMES_ACTION_TIMEOUT))
        except Exception:
            return False
        try:
            await anchor.click(timeout=_ms(timeout_s, HERMES_ACTION_TIMEOUT))
            return True
        except Exception as e:
            raise ElementNotFound(f"click svg-anchor '{svg_aria_label}'") from e

    async def is_css_visible(self, selector: str, *, timeout_s: Optional[float] = None) -> bool:
        page = self._require_page()
        try:
            await page.locator(selector).first.wait_for(
                state="visible", timeout=_ms(timeout_s, HERMES_ACTION_TIMEOUT)
            )
            return True
        except Exception:
            return False

    async def click_css(self, selector: str, *, timeout_s: Optional[float] = None) -> bool:
        """
        Click the FIRST ENABLED, visible element matching a raw CSS `selector`.

        Needed for SPAs (e.g. Facebook's composer) whose actionable control is a
        role-less/aria-labelled <div role="button"> that the accessible (role,
        name) cascade can match for visibility but not reliably click. Scopes to
        a precise selector so decoy "Post" elements elsewhere are not hit.

        Skips elements with aria-disabled="true"/disabled so a greyed-out Post
        button (before text is entered) is never clicked. Returns True if a
        clickable match was found and clicked, False otherwise. Never raises.
        """
        page = self._require_page()
        try:
            loc = page.locator(selector)
            count = await loc.count()
        except Exception:
            return False
        deadline_ms = _ms(timeout_s, HERMES_ACTION_TIMEOUT)
        for i in range(min(count, 10)):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                aria_disabled = await el.get_attribute("aria-disabled")
                if aria_disabled == "true":
                    continue
                if not await el.is_enabled():
                    continue
                await el.click(timeout=deadline_ms)
                return True
            except Exception:
                continue
        return False

    async def clear_css(self, selector: str, *, timeout_s: Optional[float] = None) -> bool:
        """
        Clear the text of the first visible element matching a CSS `selector`.

        Focuses the element, selects all (Ctrl+A), and deletes — robust for rich
        contenteditable editors (e.g. a restored draft) where a plain fill may not
        fully replace existing content. Returns True if a visible match was
        cleared, else False. Never raises; never submits.
        """
        page = self._require_page()
        timeout = _ms(timeout_s, HERMES_ACTION_TIMEOUT)
        try:
            loc = page.locator(selector)
            count = await loc.count()
        except Exception:
            return False
        for i in range(min(count, 10)):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                await el.click(timeout=timeout)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Delete")
                return True
            except Exception:
                continue
        return False

    async def count_large_images(self, selector: str, min_px: int, *, timeout_s: Optional[float] = None) -> int:
        """
        Count elements matching `selector` whose RENDERED width AND height are at
        least `min_px`. Read-only. Used to confirm an attached-image PREVIEW
        (a sizeable <img>) while excluding small avatars/icons that share the same
        selector/host. Returns 0 on absence/error; never mutates the page.
        """
        page = self._require_page()
        try:
            return int(await page.evaluate(
                """([sel, min]) => {
                    let n = 0;
                    for (const el of document.querySelectorAll(sel)) {
                        const r = el.getBoundingClientRect();
                        if (r.width >= min && r.height >= min) n++;
                    }
                    return n;
                }""",
                [selector, min_px],
            ))
        except Exception:
            return 0

    async def fill_css(self, selector: str, value: str, *, timeout_s: Optional[float] = None) -> bool:
        """
        Type `value` into the first visible element matching a raw CSS `selector`.

        Handles both native inputs/textareas and contenteditable DIVs (e.g.
        Facebook's composer editor, whose accessible name is exposed only via
        aria-placeholder and so cannot be targeted by the role/name cascade).
        Focuses the element, then fills. Returns True on success, False if no
        visible match or the fill failed. Never raises.
        """
        page = self._require_page()
        timeout = _ms(timeout_s, HERMES_ACTION_TIMEOUT)
        try:
            loc = page.locator(selector)
            count = await loc.count()
        except Exception:
            return False
        for i in range(min(count, 10)):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                await el.click(timeout=timeout)
                # Playwright .fill() supports contenteditable + inputs/textareas.
                await el.fill(value, timeout=timeout)
                return True
            except Exception:
                # Fallback: focus + type for stubborn contenteditable editors.
                try:
                    await el.focus(timeout=timeout)
                    await el.type(value, timeout=timeout)
                    return True
                except Exception:
                    continue
        return False

    # ── Diagnostics ─────────────────────────────────────────────────────────────

    async def screenshot(self, path: str) -> None:
        page = self._require_page()
        try:
            await page.screenshot(path=path)
        except Exception:
            log.debug("Hermes screenshot failed")
