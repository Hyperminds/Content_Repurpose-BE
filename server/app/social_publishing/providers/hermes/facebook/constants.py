"""Facebook (Hermes) workflow constants — URLs, limits, locators.

ONE Facebook Hermes implementation serves TWO targets:
  - target_type="profile": the authenticated user's personal timeline composer
  - target_type="page":     a specific Facebook Page's composer

There are NO separate provider/workflow classes per target. The workflow is
target-aware: it reads the target from the connected account configuration and
selects the right URL + composer locators from the tables below. All
Facebook-specific selectors live HERE so they can be updated later WITHOUT
touching the publishing architecture.

Like Instagram/Reddit Hermes, these accessibility-first locators are BEST-EFFORT
starting points for the live, obfuscated Facebook SPA and may need adjustment —
but adjusting them must never require changing the workflow's prepare → confirm →
verify flow.

Nothing here bypasses login / MFA / CAPTCHA / anti-bot: the workflow stops with
ACTION_REQUIRED whenever authentication or human verification is required.
"""

PLATFORM = "facebook"

# ── Target types ──────────────────────────────────────────────────────────────
TARGET_PROFILE = "profile"
TARGET_PAGE = "page"
TARGET_TYPES = (TARGET_PROFILE, TARGET_PAGE)
DEFAULT_TARGET_TYPE = TARGET_PROFILE

# ── URLs ──────────────────────────────────────────────────────────────────────
FB_BASE = "https://www.facebook.com"
LOGIN_URL = f"{FB_BASE}/login/"
HOME_URL = f"{FB_BASE}/"
# "me" resolves to the authenticated user's own profile without knowing the id.
PROFILE_URL = f"{FB_BASE}/me"


def page_url(page_identifier: str) -> str:
    """
    Build a Page URL from a page identifier (vanity name, numeric id, or a full
    URL). A full URL is returned as-is; otherwise it is appended to the base.
    """
    ident = (page_identifier or "").strip()
    if not ident:
        return HOME_URL
    if ident.startswith("http://") or ident.startswith("https://"):
        return ident
    return f"{FB_BASE}/{ident.lstrip('/')}"


def target_url(target_type: str, target_identifier: str = "") -> str:
    """Resolve the destination URL for a target. Profile ignores the identifier."""
    if target_type == TARGET_PAGE:
        return page_url(target_identifier)
    return PROFILE_URL


# ── Content limits ────────────────────────────────────────────────────────────
# Facebook permits very long text; we cap generously to catch runaway input.
MAX_TEXT_LENGTH = 63206
# This phase: text-only OR a single image + text. No carousel/video/reels.
MAX_MEDIA_ITEMS = 1
MIN_MEDIA_ITEMS = 0  # media is OPTIONAL (text-only posts are allowed)

# ── Supported media (image only this phase; NO video/reels/carousel) ──────────
SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png", "gif", "webp"]
SUPPORTS_VIDEO = False

# ── File input for image attach (hidden <input type=file>) ────────────────────
# The composer's hidden file input. Prefer the one scoped to the Create-post
# dialog so we never target an unrelated page-level file input. set_input_files
# writes the file DIRECTLY to this input — the "Photo/video" button is NEVER
# clicked (clicking it opens the native OS file picker and blocks).
IMAGE_FILE_INPUT_SELECTOR = "div[role='dialog'][aria-label='Create post'] input[type=file]"
IMAGE_FILE_INPUT_SELECTOR_GENERIC = "input[type=file]"

# ── Composer locators (role, name) — TARGET-AWARE ────────────────────────────
# Each value is a TUPLE OF CANDIDATES tried in order via the _first_visible
# helper. Profile and Page composers differ, so the opener candidates are keyed
# by target_type. The post dialog + Post button are largely shared.
#
# PROFILE: the timeline shows a "What's on your mind?" status entry that opens
# the create-post dialog. PAGE: the Page composer shows a similar prompt (often
# "What's on your mind, <Page>?" / "Create a post" / "Write something...").
LOC_OPEN_COMPOSER_CANDIDATES = {
    TARGET_PROFILE: [
        ("button", "What's on your mind?"),
        ("textbox", "What's on your mind?"),
        ("link", "What's on your mind?"),
        ("button", "Create a post"),
        ("button", "Write something..."),
    ],
    TARGET_PAGE: [
        ("button", "Create a post"),
        ("button", "Write something..."),
        ("button", "What's on your mind?"),
        ("textbox", "Write a post..."),
        ("button", "Write a post..."),
    ],
}

# The create-post dialog that genuinely opened. LIVE DOM confirmed the dialog
# carries aria-label="Create post". We MUST scope to that — a bare
# div[role='dialog'] also matches Facebook's Notifications/Chat flyouts and
# produced a FALSE POSITIVE (the composer looked "open" when only the
# notifications dialog was). The generic selector is kept only as a weak
# fallback for UI variants.
COMPOSER_DIALOG_SELECTOR = "div[role='dialog'][aria-label='Create post']"
COMPOSER_DIALOG_SELECTOR_GENERIC = "div[role='dialog']"
LOC_COMPOSER_DIALOG_TITLE_CANDIDATES = [
    ("heading", "Create post"),
    ("heading", "Create Post"),
]

# The main text editor inside the Create-post dialog.
#
# LIVE DOM (confirmed): the composer editor is a contenteditable DIV with
# role="textbox", and its ONLY accessible hint is `aria-placeholder="What's on
# your mind?"` — `aria-label` is null. Playwright's role/name resolution does
# NOT reliably use aria-placeholder, so the (role, name) cascade misses it, and
# the page also has decoy "Write a comment…" textboxes in the feed. We therefore
# target the editor with a DIALOG-SCOPED CSS selector keyed on aria-placeholder
# (via fill_css), scoped to div[aria-label="Create post"] so comment boxes are
# never matched. The (role, name) list is kept only as a last-resort fallback.
TEXT_EDITOR_CSS_CANDIDATES = [
    "div[aria-label='Create post'] div[role='textbox'][contenteditable='true']",
    "div[role='dialog'] div[role='textbox'][aria-placeholder^=\"What's on your mind\"]",
    "div[role='dialog'] div[role='textbox'][contenteditable='true']",
]
LOC_TEXT_EDITOR_CANDIDATES = [
    ("textbox", "What's on your mind?"),
    ("textbox", "Write something..."),
    ("textbox", "Write a post..."),
    ("textbox", "Create a public post..."),
]

# "Photo/video" control that fronts the hidden file input (image attach).
LOC_ADD_PHOTO_CANDIDATES = [
    ("button", "Photo/video"),
    ("button", "Photo/Video"),
    ("button", "Add photos/videos"),
    ("button", "Photo"),
]

# Attach verification: a blob/data <img> OR a blob-backed background preview
# (same two-signal idea proven on Instagram).
IMAGE_PREVIEW_SELECTOR = "img[src^='blob:'], img[src^='data:']"
IMAGE_PREVIEW_BG_SELECTOR = "[style*='blob:']"

# The final Post button — clicked ONLY during confirm_and_publish.
#
# Facebook's composer "Post" button is an aria-labelled role=button DIV inside
# the create-post dialog, NOT a native <button>. The accessible (role, name)
# cascade can see a "Post" element for visibility but often cannot click the
# real control (it matches decoy "Post" text or a disabled placeholder). So the
# workflow clicks via a DIALOG-SCOPED CSS selector first (click_css, which skips
# aria-disabled matches), then falls back to the (role, name) candidates.
#
# These CSS selectors are scoped to the composer dialog so unrelated "Post"
# controls elsewhere on the page are never selected. Ordered most-specific first.
POST_BUTTON_CSS_CANDIDATES = [
    "div[role='dialog'] div[aria-label='Post'][role='button']",
    "div[role='dialog'] div[aria-label='Post']",
    "div[aria-label='Create post'] div[aria-label='Post'][role='button']",
    "div[role='dialog'] [aria-label='Post'][role='button']",
]
# Accessible-name fallbacks (kept for resilience / non-Facebook-UI variants).
LOC_POST_BUTTON_CANDIDATES = [
    ("button", "Post"),
    ("div", "Post"),
]

# Authenticated signal: a composer-entry control is present on the target when
# logged in. Reused per target. Login-form signal means NOT authenticated.
LOC_LOGIN_SIGNAL_CANDIDATES = [
    ("textbox", "Email or phone number"),
    ("textbox", "Email address or phone number"),
    ("button", "Log in"),
    ("button", "Log In"),
]

# ── Success signals ───────────────────────────────────────────────────────────
# After posting, the composer dialog closes and the new post appears in the
# feed/timeline. Facebook does not reliably navigate to a canonical permalink in
# the address bar, so verification mirrors Instagram/Reddit: look for the
# composer having closed + an optional confirmation toast, then stay UNKNOWN if
# uncertain (never blind-retry).
POST_SUCCESS_TEXT_CANDIDATES = [
    ("heading", "Your post is now published"),
    ("status", "Your post is now published"),
]

# Polling (bounded).
import os as _os


def _f(name: str, default: float) -> float:
    try:
        return float(_os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(_os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Polling (bounded). EARLY-EXIT polls — a shorter interval detects composer-open
# / attach / post-result sooner (and fails sooner) without missing success.
# Env-overridable so timing can be tuned without code changes.
POST_POLL_INTERVAL_S = _f("HERMES_FB_POLL_INTERVAL_S", 0.75)
POST_POLL_MAX_CHECKS = _i("HERMES_FB_POLL_MAX_CHECKS", 24)

# Short probe timeout when trying candidate locators that may be absent.
PROBE_TIMEOUT = _f("HERMES_FB_PROBE_TIMEOUT", 2)

# Only trust permalinks on facebook.com.
FACEBOOK_HOSTS = ("facebook.com", "www.facebook.com", "m.facebook.com")
PERMALINK_MARKERS = ("/posts/", "/permalink/", "story_fbid=")

# ── Confirmation ──────────────────────────────────────────────────────────────
CONFIRM_ACTION = "confirm_facebook_publish"
