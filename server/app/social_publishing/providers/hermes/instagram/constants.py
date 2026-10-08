"""Instagram (Hermes) workflow constants — URLs, limits, locators.

IMPORTANT: Instagram's web composer is a heavily-obfuscated SPA and its
accessibility labels change often. The locators below are BEST-EFFORT starting
points and MUST be verified against the real UI (Phase 8) before the workflow
can reliably reach ACTION_REQUIRED — exactly as Reddit's composer locators had
to be corrected. Each locator lists the primary + known fallbacks so the
workflow can probe several candidates.

Nothing here bypasses login / MFA / CAPTCHA / anti-bot: the workflow stops with
ACTION_REQUIRED whenever authentication or human verification is required.
"""

PLATFORM = "instagram"

# ── URLs ──────────────────────────────────────────────────────────────────────
IG_BASE = "https://www.instagram.com"
LOGIN_URL = f"{IG_BASE}/accounts/login/"
# Instagram opens the create-post composer as an in-page modal from the home
# feed (there is no stable standalone composer URL for web). We navigate to the
# base and open the composer via the "Create"/"New post" control.
HOME_URL = f"{IG_BASE}/"

def profile_url(username: str) -> str:
    return f"{IG_BASE}/{username}/"

# ── Content limits (Instagram documented UI limits) ───────────────────────────
MAX_CAPTION_LENGTH = 2200
# Single image only in this phase. Do NOT raise without adding carousel support.
MAX_MEDIA_ITEMS = 1
MIN_MEDIA_ITEMS = 1  # Instagram requires media — there is no text-only post.

# ── Supported media (Phase: image only; NO video/reels/carousel) ──────────────
SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png"]
SUPPORTS_VIDEO = False

# ── Semantic locators (role, name) — accessibility-first, no pixel coords ─────
# Each is a TUPLE OF CANDIDATES tried in order (the workflow's _first_visible
# helper clicks/reads the first that appears). Adjust after live inspection.
#
# Open the create menu. Live inspection (2024/2025 web UI) shows the left nav
# exposes a "Create" control; clicking it reveals a flyout whose FIRST item is
# "Post". Names are matched EXACTLY by the adapter, so "Post" will not also
# match "New post". Ordered most-likely first; each is tried until one is
# visible. (Older UIs used "New post" directly — kept as a fallback.)
LOC_CREATE_CANDIDATES = [
    ("link", "Create"),
    ("button", "Create"),
    ("link", "New post"),
    ("button", "New post"),
]
# The "Post" item inside the Create flyout that actually opens the composer.
# NOTE: the Create flyout also lists other options (e.g. "AI content"); we want
# exactly "Post". Exact matching in the adapter prevents matching "New post".
LOC_CREATE_POST_SUBMENU_CANDIDATES = [
    ("link", "Post"),
    ("button", "Post"),
    ("menuitem", "Post"),
    ("div", "Post"),
]
# The "Select from computer" control fronts a hidden <input type=file>. We attach
# to the file input directly via the selector below (mirrors Reddit).
LOC_SELECT_FROM_COMPUTER_CANDIDATES = [
    ("button", "Select from computer"),
    ("button", "Select From Computer"),
]
IMAGE_FILE_INPUT_SELECTOR = "input[type=file]"

# ── Confirmed open-composer path (verified against the live UI) ───────────────
# The create flow is driven by two icon-only controls whose CLICK is owned by an
# ancestor <a href="#">, not the SVG itself. We resolve each via
# svg[aria-label=<label>].closest("a[href='#']") (adapter.click_svg_anchor):
#   1) sidebar New Post  -> svg[aria-label="New post"] -> opens the Create flyout
#   2) flyout Post item  -> svg[aria-label="Post"]     -> opens the composer modal
# These labels are the exact ones observed live. Do NOT use a generic
# get_by_role("link","Post"): decoy profile links also match "Post".
NEW_POST_SVG_LABEL = "New post"
POST_FLYOUT_SVG_LABEL = "Post"

# Signals that the create-post MODAL genuinely opened (confirmed indicators).
# The composer modal is an ARIA dialog labelled "Create new post"; it also shows
# a "Select from computer" button and (after engaging it) mounts a file input.
COMPOSER_DIALOG_SELECTOR = "div[role='dialog'][aria-label='Create new post']"
COMPOSER_DIALOG_SELECTOR_FALLBACK = "div[role='dialog']"
LOC_COMPOSER_DIALOG_TITLE_CANDIDATES = [
    ("heading", "Create new post"),
    ("heading", "Crop"),
]
# "Select from computer" control — a strong composer-open signal.
LOC_SELECT_FROM_COMPUTER_SIGNAL = [
    ("button", "Select from computer"),
    ("button", "Select From Computer"),
]
# After selecting media, Instagram shows a crop step, then "Next" (twice:
# crop → edit/filters → caption). We click Next until the caption field appears.
LOC_NEXT_CANDIDATES = [
    ("button", "Next"),
    ("div", "Next"),
]
# The caption editor (a contenteditable div with role="textbox"). The current
# live UI labels it "Add a caption..." (confirmed via live diagnostics:
# tag=div, role=textbox, aria-label/aria-placeholder="Add a caption...",
# contenteditable=true, visible). Older/variant UIs used "Write a caption…";
# those are kept as fallbacks since Instagram's labels drift. Ordered
# most-likely-first; _fill_caption tries each until one is visible.
LOC_CAPTION_CANDIDATES = [
    ("textbox", "Add a caption..."),
    ("textbox", "Add a caption…"),
    ("textbox", "Add a caption"),
    ("textbox", "Write a caption..."),
    ("textbox", "Write a caption…"),
    ("textbox", "Write a caption"),
]
# The final Share control — clicked ONLY during confirm_and_publish.
LOC_SHARE_CANDIDATES = [
    ("button", "Share"),
    ("div", "Share"),
]
# Authenticated signal: the composer/home exposes a "New post"/Create control
# and a profile affordance only when logged in. We treat the presence of the
# Create control on the home page as "authenticated". If it's absent AND we can
# see a login form, we return ACTION_REQUIRED (login).
LOC_AUTH_SIGNAL_CANDIDATES = LOC_CREATE_CANDIDATES
# Login-form signal — presence means NOT authenticated.
LOC_LOGIN_SIGNAL_CANDIDATES = [
    ("textbox", "Phone number, username, or email"),
    ("textbox", "Username, or email"),
    ("button", "Log in"),
]

# Attach verification: after selecting media, a crop/preview <img> (blob:) is
# rendered. Same idea as Reddit's image-attach verification.
IMAGE_PREVIEW_SELECTOR = "img[src^='blob:'], img[src^='data:']"
# Instagram's current composer renders the crop preview as a blob-backed CSS
# background (e.g. style="background-image: url('blob:…')") rather than an <img>,
# so the <img> selector above alone misses a successful attach. Live diagnostics
# confirmed the preview surfaces as an element whose inline style references a
# blob: URL. This selector matches that blob-backed background preview.
IMAGE_PREVIEW_BG_SELECTOR = "[style*='blob:']"

# ── Success signals ───────────────────────────────────────────────────────────
# After sharing, Instagram shows a "Your post has been shared." confirmation and
# the composer closes. A canonical permalink is /p/<shortcode>/. The address bar
# does NOT reliably navigate to it, so verification mirrors Reddit: check for the
# shared-confirmation text, then reconcile via the profile grid if needed.
POST_SHARED_TEXT_CANDIDATES = [
    ("heading", "Your post has been shared."),
    ("heading", "Your post has been shared"),
]
PERMALINK_MARKER = "/p/"

# Polling for the shared confirmation / permalink (bounded).
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


# Polling for composer-open / attach / shared-confirmation / permalink (bounded).
# These are EARLY-EXIT polls, so a shorter interval detects success (and failure)
# sooner without missing it — the main lever for snappier posting. Env-overridable
# so timing can be tuned without code changes.
SHARE_POLL_INTERVAL_S = _f("HERMES_IG_POLL_INTERVAL_S", 0.75)
SHARE_POLL_MAX_CHECKS = _i("HERMES_IG_POLL_MAX_CHECKS", 24)

# Short probe timeout when trying candidate locators that may be absent.
PROBE_TIMEOUT = _f("HERMES_IG_PROBE_TIMEOUT", 2)

# Only trust permalinks on instagram.com.
INSTAGRAM_HOSTS = ("instagram.com", "www.instagram.com")

# ── Confirmation ──────────────────────────────────────────────────────────────
CONFIRM_ACTION = "confirm_instagram_publish"
