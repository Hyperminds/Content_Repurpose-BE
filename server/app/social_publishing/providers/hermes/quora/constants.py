"""Quora (Hermes) workflow constants — URLs, limits, locators.

Mirrors the Facebook/Instagram Hermes constants. ALL Quora-specific selectors
live HERE so they can be tuned later WITHOUT touching the workflow or the
prepare → confirm → verify architecture.

These accessibility-first locators are BEST-EFFORT starting points for Quora's
live, obfuscated SPA and WILL likely need adjustment against the real logged-in
UI (same as Facebook/Instagram required). Adjusting them must never require
changing the workflow flow. We deliberately avoid fragile generated CSS classes
and prefer role/aria-label/text + a small set of robust fallbacks.

Nothing here bypasses login / MFA / CAPTCHA / anti-bot: the workflow stops with
ACTION_REQUIRED whenever authentication or human verification is required.
"""

PLATFORM = "quora"

# ── URLs ──────────────────────────────────────────────────────────────────────
QUORA_BASE = "https://www.quora.com"
LOGIN_URL = f"{QUORA_BASE}/"
# Quora opens the create-post composer as an in-page modal from the home feed.
HOME_URL = f"{QUORA_BASE}/"


def profile_url(username: str) -> str:
    uname = (username or "").strip().lstrip("@")
    return f"{QUORA_BASE}/profile/{uname}" if uname else HOME_URL


# ── Content limits ────────────────────────────────────────────────────────────
# Quora posts allow long text; cap generously to catch runaway input.
MAX_TEXT_LENGTH = 30000
# This phase: text-only OR a single image + text. No carousel/video.
MAX_MEDIA_ITEMS = 1
MIN_MEDIA_ITEMS = 0  # media is OPTIONAL (text-only posts are allowed)

# ── Supported media (image only this phase; NO video) ─────────────────────────
SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png", "gif", "webp"]
SUPPORTS_VIDEO = False

# ── File input for image attach (hidden <input type=file>) ────────────────────
# CONFIRMED via live DOM inspection: the Quora composer mounts TWO hidden file
# inputs from the start — an IMAGE one (accept="image/*", multiple) and a VIDEO
# one (accept=".mp4,.mov,.webm"). We MUST target the image-accepting input; a
# bare "input[type=file]" matches both and .first could hit the video input,
# silently dropping the image. set_input_files writes the file DIRECTLY to this
# input — the "Add image" control is NEVER clicked (no OS picker), exactly like
# the Facebook/Instagram workflows. Image-accepting selectors first, then
# broader fallbacks.
IMAGE_FILE_INPUT_SELECTOR = "div[role='dialog'] input[type=file][accept*='image']"
IMAGE_FILE_INPUT_SELECTOR_GENERIC = "input[type=file][accept*='image']"

# ── Composer-open controls (role, name) — candidate list tried in order ───────
# Quora's home/profile exposes a "Create Post" / "Post" / "Add a post" control
# that opens the create-post composer modal. Ordered most-likely first; each is
# tried until one is visible.
LOC_OPEN_COMPOSER_CANDIDATES = [
    ("button", "Create Post"),
    ("button", "Create post"),
    ("button", "Post"),
    ("button", "Add a post"),
    ("button", "Write a post"),
    ("link", "Create Post"),
]

# The create-post dialog that genuinely opened. Scope to a labelled dialog where
# possible so unrelated Quora dialogs don't produce a false positive.
COMPOSER_DIALOG_SELECTOR = "div[role='dialog']"
COMPOSER_DIALOG_SELECTOR_GENERIC = "div[role='dialog']"
LOC_COMPOSER_DIALOG_TITLE_CANDIDATES = [
    ("heading", "Create Post"),
    ("heading", "Create post"),
    ("heading", "Add Post"),
]

# The main text editor inside the dialog. Quora uses a rich contenteditable
# editor. Its accessible hint is typically a placeholder like "Say something" /
# "Write something" / "What do you want to ask or share?". We target it with a
# DIALOG-SCOPED CSS selector first (via fill_css) and keep (role, name) as a
# fallback.
TEXT_EDITOR_CSS_CANDIDATES = [
    "div[role='dialog'] div[contenteditable='true']",
    "div[role='dialog'] [role='textbox'][contenteditable='true']",
    "div[role='dialog'] textarea",
]
LOC_TEXT_EDITOR_CANDIDATES = [
    ("textbox", "Say something..."),
    ("textbox", "Write something..."),
    ("textbox", "What do you want to ask or share?"),
    ("textbox", "Say something"),
]

# "Add image" / media control — kept for reference only. We DO NOT click it
# (that opens the OS file picker); media is set directly on the hidden input.
LOC_ADD_IMAGE_CANDIDATES = [
    ("button", "Add image"),
    ("button", "Image"),
    ("button", "Add photo"),
]

# Attach verification. CONFIRMED via live inspection: unlike Instagram/Facebook
# (blob: previews), Quora uploads the image to its CDN IMMEDIATELY on attach and
# renders a quoracdn.net <img> preview inside the composer dialog. So the
# reliable Quora attach signal is a composer-scoped CDN preview image. We still
# accept blob:/data: previews as a fallback for UI variants.
IMAGE_PREVIEW_SELECTOR = "img[src^='blob:'], img[src^='data:']"
IMAGE_PREVIEW_BG_SELECTOR = "[style*='blob:']"
# A dialog-scoped Quora-CDN preview image. The attached photo renders as a
# sizeable <img src="https://qph.*.quoracdn.net/...">; scoping to the dialog +
# the CDN host avoids matching the small (40px) avatar elsewhere. The workflow
# additionally requires a minimum rendered size to exclude avatars/icons.
IMAGE_PREVIEW_CDN_SELECTOR = "div[role='dialog'] img[src*='quoracdn']"
# Minimum rendered width/height (px) for a CDN <img> to count as the attached
# photo preview (avatars are ~40px).
IMAGE_PREVIEW_MIN_SIZE = 120

# ── Final Post button — clicked ONLY during confirm_and_publish ───────────────
# CONFIRMED via live DOM inspection: Quora's composer submit is a real <button>
# with VISIBLE TEXT "Post" and NO aria-label, carrying the stable, non-obfuscated
# class token `puppeteer_test_modal_submit` (a test hook Quora ships). We click
# via click_css: the test-hook class first (most robust), then text-scoped
# fallbacks inside the dialog. The accessible (role, name) list is the final
# fallback. NOTE: it is a <button> (not a role=button div) and has no aria-label,
# which is why the earlier aria-label-scoped selectors never matched.
POST_BUTTON_CSS_CANDIDATES = [
    "button.puppeteer_test_modal_submit",
    "div[role='dialog'] button.puppeteer_test_modal_submit",
    "div[role='dialog'] button:has-text('Post')",
]
LOC_POST_BUTTON_CANDIDATES = [
    ("button", "Post"),
    ("div", "Post"),
]

# Controls to CLOSE/DISCARD a stale composer before composing a new post, so a
# leftover draft can never leak its text/image into the next post. Best-effort —
# the workflow also re-navigates to a clean home page as the authoritative reset.
LOC_COMPOSER_CLOSE_CANDIDATES = [
    ("button", "Close"),
    ("button", "Cancel"),
    ("button", "Dismiss"),
]
LOC_DISCARD_CONFIRM_CANDIDATES = [
    ("button", "Discard"),
    ("button", "Discard post"),
    ("button", "Delete"),
    ("button", "Yes"),
]

# Login-form signal — presence means NOT authenticated.
LOC_LOGIN_SIGNAL_CANDIDATES = [
    ("textbox", "Email"),
    ("button", "Login"),
    ("button", "Log In"),
    ("button", "Continue with Google"),
]
# Authenticated signal: a composer-entry control is present when logged in.
LOC_AUTH_SIGNAL_CANDIDATES = LOC_OPEN_COMPOSER_CANDIDATES

# ── Success signals ───────────────────────────────────────────────────────────
# After posting, the composer dialog closes and the post appears in the feed.
# Quora does not reliably navigate to a canonical permalink in the address bar,
# so verification mirrors Facebook: composer-closed is the primary signal, with
# an optional success toast / permalink when present. Uncertain → UNKNOWN
# (never blind-retry).
POST_SUCCESS_TEXT_CANDIDATES = [
    ("heading", "Your post has been added"),
    ("status", "Post added"),
]
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
POST_POLL_INTERVAL_S = _f("HERMES_QUORA_POLL_INTERVAL_S", 0.75)
POST_POLL_MAX_CHECKS = _i("HERMES_QUORA_POLL_MAX_CHECKS", 24)
# Short probe timeout when trying candidate locators that may be absent.
PROBE_TIMEOUT = _f("HERMES_QUORA_PROBE_TIMEOUT", 2)

# Only trust permalinks on quora.com.
QUORA_HOSTS = ("quora.com", "www.quora.com")
PERMALINK_MARKERS = ("/profile/", "-post/", "/q/")

# ── Confirmation ──────────────────────────────────────────────────────────────
CONFIRM_ACTION = "confirm_quora_publish"
